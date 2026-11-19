"""按模型明确请求补入冻结原文；有界续跑，不截断、不自动确认归属。"""
import copy
import json
from pathlib import Path

from bounded_read import raw_size, checkpoint
from runtime import digest, moment, VERSION, REVIEW_POLICY

# 仅限制后台执行量；达到上限保留待核，不转换成员工材料不足或低分。
MAX_ROUNDS = 3
MAX_EVENTS = 24
MAX_NODE_ROUNDS = 24
UNIT_BYTES = 120_000


class InvalidReadback(ValueError):
    """已收到的补读请求违反合同，可由既有有界修订处理，原回执保留。"""


def expand(unit, packet, requested):
    if (not isinstance(requested, list) or not requested or len(requested) > MAX_EVENTS
            or any(not isinstance(e, str) for e in requested) or len(set(requested)) != len(requested)):
        raise ValueError('原文补读须请求1至24个不同事件')
    source = {e['event']: e for e in packet['events']}
    present = {e['event']: e for e in unit['events']}
    if len(source) != len(packet['events']) or len(present) != len(unit['events']):
        raise ValueError('原文补读来源事件重复')
    result = copy.deepcopy(unit)
    from navigation_contract import discard_directory
    discard_directory(result)
    added = []
    for eid in requested:
        if eid not in source:
            raise ValueError('请求事件不在本人的冻结来源中；禁止猜测、跨人或跨期补取')
        if eid in present:
            raise ValueError('请求事件已经完整提供，须核对具体缺失，不能循环重发')
        event = copy.deepcopy(source[eid])
        if moment(event['at']) >= moment(packet['until']):
            raise ValueError('禁止补读评价截止之后的材料倒填当时判断')
        # 补读不改变已有本人归属；原始user角色仍按背景送入，不能自动成为本人动作。
        event.update(event_id=eid, actor_class='context', human_spans=[])
        result['events'].append(event)
        added.append(dict(event=eid, source_event_sha256=digest(source[eid])))
    result.setdefault('context_readback', []).append(dict(
        packet_sha256=digest(packet), requested_events=added,
        limitation='补入完整已保存原文；不证明上游未截断，不改变本人归属、目标或计分范围'))
    if raw_size(result) > UNIT_BYTES:
        from navigation_access import ReadbackCapacity
        raise ReadbackCapacity('完整补读超出当前评分容量，保留请求待分层处理，不裁掉原文或反证')
    return result


def complete(unit, packet, directory, client, receive, progress=lambda *args: None, navigation_access=None, feedback=None, validate_final=None):
    """每轮独立绑定输入和回执；恢复时重验同一链，不覆盖前序候选。"""
    root = Path(directory)
    if str(unit.get('user_id')) != str(packet['user_id']):
        raise ValueError('补读身份与原评审对象不同，禁止跨人补取')
    if navigation_access is not None:unit=navigation_access.start(unit)
    base = dict(unit_sha256=digest(unit), packet_sha256=digest(packet))
    state = root/'context-readback'/'input.json'
    if state.exists() and json.loads(state.read_text()) != base:
        raise ValueError('补读的冻结来源或评审对象变化，不能沿用旧链')
    checkpoint(state, base)
    current = unit
    receipt = root/'review.receipt.json'
    event_rounds=node_rounds=0
    for index in range(MAX_ROUNDS + MAX_NODE_ROUNDS + 1):
        # 仅自动修订轮携带前一候选与复查意见；补读各轮沿用同一份，保证请求可复验。
        from review_protocol import active as protocol_active, call_checked
        protocol=protocol_active(client)
        def prepare(value):
            requested=value.get('needs_events',[]);nodes=value.get('needs_nodes',[])
            if nodes:
                if navigation_access is None:raise ValueError('当前未提供分层导航，不接受节点补读')
                if node_rounds>=MAX_NODE_ROUNDS:
                    from pipeline import Paused
                    raise Paused('failed','分层补读达到本轮执行上限',reason_code='request_capacity_exceeded')
                return navigation_access.expand(current,nodes,str(index+1))
            if event_rounds>=MAX_ROUNDS:
                from pipeline import Paused
                raise Paused('failed','事件补读达到本轮执行上限',reason_code='request_capacity_exceeded')
            if navigation_access is not None:navigation_access.verify(current)
            result=expand(current,packet,requested)
            if navigation_access is not None:
                from navigation_access import set_remaining, ReadbackCapacity
                if navigation_access.directory_mode is not None:
                    set_remaining(result,navigation_access.unit_bytes)
                    if raw_size(result)>navigation_access.unit_bytes:
                        raise ReadbackCapacity('完整事件补读超过当前实例容量，保留原状态与原文，不发送后续请求')
                navigation_access.remember(result)
            return result
        if protocol:
            record,receipt,next_unit=call_checked(current,receipt,client,receive,feedback=feedback,
                validate_final=validate_final,prepare_readback=prepare)
        else:
            record = client.call(current, receipt) if feedback is None else client.call(current, receipt, feedback=feedback)
        value = receive(record)
        requested = value.get('needs_events')
        nodes=value.get('needs_nodes',[])
        if not requested and not nodes:
            return current, record, receipt
        if value.get('evidence') or value.get('period_reviews'):
            raise InvalidReadback('补读请求不能同时提交评分；原回执保留，禁止部分评分冒充已完成')
        fields={'version','review_policy','key','evidence','period_reviews','needs_events'}
        from single_pass import active
        if active(client):
            fields.add('presentation')
            if value.get('presentation') is not None:raise InvalidReadback('补读时不能同时提交评分说明')
        if navigation_access is not None:fields.add('needs_nodes')
        if (set(value) != fields
                or value.get('version') != VERSION or value.get('review_policy') != REVIEW_POLICY
                or value.get('key') != current['key'] or value['evidence'] != [] or value['period_reviews'] != []):
            raise ValueError('补读请求未绑定当前版本和评审对象，禁止追加请求')
        if requested and nodes:raise ValueError('每轮只选择事件补读或分层访问，禁止混合请求')
        if nodes:
            if navigation_access is None:raise ValueError('当前未提供分层导航，不接受节点补读')
            if node_rounds>=MAX_NODE_ROUNDS:raise ValueError('分层补读达到本轮执行上限，保留原回执待核')
            from navigation_access import ReadbackCapacity
            try:
                if not protocol:next_unit=prepare(value)
            except ReadbackCapacity as ex:raise InvalidReadback(str(ex)) from None
            node_rounds+=1
        else:
            if event_rounds>=MAX_ROUNDS:raise ValueError('补读达到本轮执行上限，保留全部回执待核，不无限追加模型请求')
            if not protocol:next_unit=prepare(value)
            event_rounds+=1
        stage = root/'context-readback'/str(index + 1)
        checkpoint(stage/'unit.json', next_unit)
        checkpoint(stage/'request.json', dict(previous_unit_sha256=digest(current),
            previous_response_sha256=digest(record), requested_events=requested,requested_nodes=nodes,
            next_unit_sha256=digest(next_unit)))
        progress('reviewing', index + 1, MAX_ROUNDS + (MAX_NODE_ROUNDS if navigation_access else 0),
            '按明确请求补读冻结原文，先前回执保留')
        current = next_unit
        receipt = stage/'review.receipt.json'
    raise AssertionError('补读循环未到达终态')
