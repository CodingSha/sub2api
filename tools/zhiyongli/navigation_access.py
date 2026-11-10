"""分层上下文的受控访问；只展开已展示节点，原文不自动认领为本人。"""
import copy
import json
from pathlib import Path

from bounded_read import raw_size
from context_navigation import INPUT_BYTES
from runtime import atomic, digest, moment

VERSION = 'navigation-access-20261109.1'
UNIT_BYTES = 120_000


def freeze(path, value):
    path=Path(path)
    if path.exists() and json.loads(path.read_text())!=value:
        raise ValueError('分层访问的冻结输入、查询或结果发生变化，不能覆盖原记录')
    atomic(path,value)


class Access:
    def __init__(self, navigation, directory, native=None):
        self.navigation=navigation;self.root=Path(directory);self.native=native
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.tree=json.loads((navigation.root/'tree.json').read_text())
        leaves=json.loads((navigation.root/'leaves.json').read_text())
        if (self.tree['scope']!=navigation.identity or leaves['scope']!=navigation.scope
                or self.tree['leaves_sha256']!=digest(leaves)):
            raise ValueError('分层访问的树、叶索引与冻结来源不符')
        self.identity=dict(version=VERSION,tree_sha256=digest(self.tree),leaves_sha256=digest(leaves),
            source_scope=navigation.scope,packet_sha256=digest(navigation.packet))
        self.leaf_count=len(leaves['node_ids'])
        self.navigation.load(self.tree['root'])
        freeze(self.root/'input.json',self.identity)

    def remember(self,unit):
        proof=dict(identity=self.identity,unit_sha256=digest(unit))
        freeze(self.root/'states'/(proof['unit_sha256']+'.json'),proof)

    def start(self, unit):
        packet=self.navigation.packet
        periods=unit.get('periods',[dict(start=unit.get('since'),end=unit.get('until'))])
        if (str(unit.get('user_id'))!=str(packet['user_id']) or len(periods)!=1
                or moment(periods[0]['start'])!=moment(packet['since'])
                or moment(periods[0]['end'])!=moment(packet['until'])):
            raise ValueError('分层访问的人员或评价时窗不同，禁止跨人或将整周导航用于月内片段')
        if unit.get('context_access'):raise ValueError('初始评审对象已含分层访问状态，须从原始冻结对象续接')
        result=copy.deepcopy(unit)
        result['context_access']=dict(version=VERSION,scope=self.navigation.scope,
            root=self.navigation.descriptor(self.tree['root']),available_node_ids=[self.tree['root']],
            inspected_node_ids=[],raw_node_ids=[],observations=[],raw_read_fragments=0,
            total_indexed_fragments=self.leaf_count,
            limitation='摘要仅作导航；这里只统计实际补读原文范围，不代表全部原文已用于评分或语义核验通过')
        if raw_size(result)>UNIT_BYTES:
            raise ValueError('初始分层评审材料超过评分容量，须继续分层综合，本次不发送模型请求')
        self.remember(result)
        return result

    def verify(self,unit):
        proof_path=self.root/'states'/(digest(unit)+'.json')
        proof=json.loads(proof_path.read_text()) if proof_path.exists() else None
        if proof!=dict(identity=self.identity,unit_sha256=digest(unit)):
            raise ValueError('分层访问状态未绑定已提供的实际材料，禁止伪造节点暴露范围')
        access=unit.get('context_access',{})
        if access.get('scope')!=self.navigation.scope:raise ValueError('分层访问范围不同')

    def check(self, unit, queries):
        self.verify(unit)
        access=unit['context_access']
        if not isinstance(queries,list) or not queries or len(queries)>24:
            raise ValueError('分层补读须请求1至24个节点')
        seen=set()
        for query in queries:
            if not isinstance(query,dict) or set(query)!={'node_id','mode'}:
                raise ValueError('分层补读字段与固定合同不符')
            key=query['node_id'];mode=query['mode']
            if not isinstance(key,str) or key not in access['available_node_ids'] or mode not in {'inspect','raw'}:
                raise ValueError('只可访问已经展示的节点及合法模式')
            if key in seen:raise ValueError('同轮节点请求重复')
            seen.add(key)
            if key in access['raw_node_ids'] or mode=='inspect' and key in access['inspected_node_ids']:
                raise ValueError('该节点内容已经提供，禁止循环补读：'+key+'（已提供内容见context_access.observations）')
            if mode=='raw' and self.navigation.load(key)['kind']!='leaf':
                raise ValueError('原文补读须先遍历到叶节点，不能把摘要冒充原文：'+key+'是分支节点，先用inspect展开，再对其叶节点用raw')

    def raw_event(self, unit, value):
        original=self.navigation.events[value['event_id']]
        if moment(original['at'])>=moment(self.navigation.packet['until']):
            raise ValueError('禁止补读评价截止后的原文')
        if unit.get('model_view'):
            from model_view import expose
            if self.native is None:raise ValueError('有界读取缺完整原生本人映射')
            expose(self.native,unit,value)
        # 完整来源已在材料中时复用该引用；不重复正文，也不更改原有本人身份。
        for event in unit['events']:
            if event['event']==original['event'] and event['text']==value['text']:
                return event['event']
        key=original['event']+'-span-'+str(value['start'])+'-'+str(value['end'])
        prior=next((e for e in unit['events'] if e['event']==key),None)
        if prior is not None:
            if prior['text']!=value['text']:raise ValueError('补读原文标识与已提供片段冲突')
        else:
            event=copy.deepcopy(original)
            event.update(event=key,event_id=key,text=value['text'],actor_class='context',human_spans=[],
                source_event=original['event'],source_span=[value['start'],value['end']])
            unit['events'].append(event)
        return key

    def expand(self, unit, queries, stage):
        self.check(unit,queries)
        values=[]
        for query in queries:
            value=self.navigation.read([query['node_id']],raw=query['mode']=='raw')['nodes'][0]
            values.append(dict(mode=query['mode'],value=value))
        if raw_size(values)>INPUT_BYTES:
            raise ValueError('分层补读合计超过单次容量，须减少节点，不截断')
        result=copy.deepcopy(unit);access=result['context_access'];available=set(access['available_node_ids'])
        for entry in values:
            value=entry['value'];key=value['node_id']
            if entry['mode']=='raw':
                event=self.raw_event(result,value);access['raw_node_ids'].append(key)
                access['observations'].append(dict(node_id=key,mode='raw',event=event,
                    source_event=value['event_id'],source_span=[value['start'],value['end']],source_event_sha256=value['source_event_sha256']))
            else:
                available.update(value.get('children',[]));access['inspected_node_ids'].append(key)
                access['observations'].append(dict(node_id=key,mode='inspect',node=value))
        access.update(available_node_ids=sorted(available),raw_read_fragments=len(access['raw_node_ids']))
        if raw_size(result)>UNIT_BYTES:
            raise ValueError('分层补读后的完整材料超过评分容量，须继续分层综合，不裁掉反证或原文')
        proof=dict(identity=self.identity,previous_unit_sha256=digest(unit),queries=queries,next_unit_sha256=digest(result))
        freeze(self.root/'queries'/(stage+'.json'),proof)
        self.remember(result)
        return result


def query_schema(unit):
    from review_schema import obj, array, enum
    return dict(array(obj(dict(node_id=enum(*unit['context_access']['available_node_ids']),
        mode=enum('inspect','raw')))),maxItems=24)
