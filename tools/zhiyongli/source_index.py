"""实验原文索引：程序建目录，模型按需读原文，不伪造首读或语义结论。"""
import json
import os
from pathlib import Path

from bounded_read import raw_size, checkpoint
from context_navigation import Navigation, MAX_CHILDREN
from runtime import atomic, digest

MODE = 'evidence-first-20261117.1'
FRAGMENT_BYTES = 12_000
LIMITATION = '程序目录仅证明原文可访问，未逐页模型阅读；不能据目录、预览或未请求背景推断无失败、无反证或本人已完成。'


def enabled(client):
    return getattr(client, 'evidence_first', False) is True


def open_navigation(directory, packet):
    path = Path(directory)/'input.json'
    prior = json.loads(path.read_text()) if path.exists() else {}
    if prior.get('source_access_mode') == MODE:
        return SourceIndex(directory, packet)
    return Navigation(directory, packet)


class SourceIndex(Navigation):
    """复用原文访问合同及内容寻址节点；没有模型摘要、阅读回执或自动本人归属。"""
    def __init__(self, directory, packet):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.packet = packet
        self.events = {e['event']: e for e in packet['events']}
        if len(self.events) != len(packet['events']):
            raise ValueError('原文索引来源事件标识重复')
        self.local_identity = dict(version=MODE, user_id=packet['user_id'], user_email=packet['user_email'],
            since=packet['since'], until=packet['until'], workday_scope_sha256=digest(packet.get('workday_scope')))
        self.scope = digest(self.local_identity)
        self.identity = dict(self.local_identity, packet_sha256=digest(packet), source_access_mode=MODE)
        checkpoint(self.root/'input.json', self.identity)
        self.path = self.root/'nodes.sqlite'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS nodes(id TEXT PRIMARY KEY,body TEXT NOT NULL)')
        os.chmod(self.path, 0o600)

    def _notes(self):
        for event in self.packet['events']:
            text = event['text']; start = 0
            while start < len(text) or start == 0 and not text:
                low, high = start, min(len(text), start+FRAGMENT_BYTES)
                while low < high:
                    end = (low+high+1)//2
                    if raw_size(dict(text=text[start:end])) <= FRAGMENT_BYTES:
                        low = end
                    else:
                        high = end-1
                if low == start and text:
                    raise ValueError('原文字符无法装入索引片段')
                yield dict(event_id=event['event'], start=start, end=low, summary=LIMITATION,
                    limitations=['尚未由模型阅读，不是空事实或已核证据'], open_questions=[])
                if low == len(text):
                    break
                start = low

    def build(self):
        # leaves仍核对每个事件的连续字符覆盖；没有背景删减或关键词筛选。
        keys = self.leaves(list(self._notes()))
        if not keys:
            raise ValueError('空来源不建立原文索引')
        leaf_value = dict(scope=self.scope, node_ids=keys)
        checkpoint(self.root/'leaves.json', leaf_value)
        level = 0
        while len(keys) > 1:
            next_keys = []
            with self.connect() as db:
                for offset in range(0, len(keys), MAX_CHILDREN):
                    children = keys[offset:offset+MAX_CHILDREN]
                    # 仅汇总位置元数据；让模型能选中其他请求的分支，不把目录当语义摘要。
                    locators = []
                    for child in children:
                        node = self.load(child, db)
                        if node['kind'] == 'leaf':
                            event = self.events[node['note']['event_id']]
                            locators.append(dict(first_event=event['event'], last_event=event['event'],
                                first_at=event.get('at'), last_at=event.get('at'), fragments=1))
                        else:
                            locators.append(node['locator'])
                    locator = dict(first_event=locators[0]['first_event'], last_event=locators[-1]['last_event'],
                        first_at=locators[0]['first_at'], last_at=locators[-1]['last_at'],
                        fragments=sum(item['fragments'] for item in locators),
                        order='冻结包顺序的首尾位置，非完整时间范围或语义分类')
                    value = dict(summary=LIMITATION, requirements='未作语义判断',
                        actions_and_results='未作语义判断', counterevidence='尚未检查，不能推断不存在',
                        unresolved='须沿目录读取关联原文及反证', covered_child_ids=children)
                    next_keys.append(self.save(dict(kind='branch', children=children, value=value,
                        source_access_mode=MODE, locator=locator), db))
            keys = next_keys; level += 1
        result = dict(scope=self.identity, root=keys[0], levels=level,
            leaves_sha256=digest(leaf_value), limitation=LIMITATION)
        checkpoint(self.root/'tree.json', result)
        return result

    def descriptor(self, key, db=None):
        value = super().descriptor(key, db)
        node = self.load(key, db)
        if node['kind']=='leaf':
            # 程序目录没有语义事实；共同限制只声明一次，避免每个定位重复挤占正文。
            value['note']={k:v for k,v in value['note'].items() if k not in ('summary','limitations','open_questions')}
        value['source_access_mode'] = MODE
        if node['kind'] == 'branch':
            value['locator'] = node['locator']
        if node['kind'] == 'leaf':
            event = self.events[node['note']['event_id']]
            value.update(event_id=event['event'], request_id=event.get('request_id'),
                protocol_role=event.get('protocol_role'), timestamp_unknown=event.get('timestamp_unknown'),
                timestamp_basis=event.get('timestamp_basis'), truncated=event.get('truncated'),
                preview=event['text'][node['note']['start']:node['note']['end']][:80],
                preview_is_complete=node['note']['end']-node['note']['start'] <= 80)
        return value

    def compact_access(self, unit):
        """只回收重复的程序目录包装；全部原文、已读定位及可访问节点保持。"""
        for observation in unit['context_access']['observations']:
            if observation['mode']!='inspect':continue
            node=observation['node']
            if node.get('source_access_mode')!=MODE:
                raise ValueError('不能把模型语义导航当成程序目录压缩')
            # value只有共同的未读声明和重复children，绝不用于旧模型语义树。
            node.pop('value',None)
            node.pop('scope',None)

    def related(self, unit):
        """同人、非空同会话与同请求只提供定位线索，不宣称同任务或已阅读。"""
        requests = {(str(e.get('source_user_id')), e.get('session_id'), e.get('request_id'))
            for e in unit['events'] if e.get('source_user_id') is not None
            and str(e['source_user_id']) == str(self.packet['user_id'])
            and e.get('session_id') and e.get('request_id')}
        leaves = json.loads((self.root/'leaves.json').read_text())
        ids = []; descriptors = []
        with self.connect() as db:
            for key in leaves['node_ids']:
                node = self.load(key, db); event = self.events[node['note']['event_id']]
                if (str(event.get('source_user_id')), event.get('session_id'), event.get('request_id')) not in requests:
                    continue
                if event['actor_class'] == 'candidate_user':
                    continue
                ids.append(key)
                if len(descriptors) < 24:
                    descriptors.append(self.descriptor(key, db))
        return dict(nodes=descriptors, total_fragments=len(ids), complete=len(ids) == len(descriptors),
            limitation='仅定位同请求背景；未读原文，其他请求及后续反证仍可能相关，不能据此排除。')

    def initial_links(self, unit, maximum, payload=None):
        from navigation_access import set_remaining
        from runtime import MAX_REQUEST_BYTES
        import copy
        links = self.related(unit)
        if not links['nodes']:
            return unit
        result = copy.deepcopy(unit); access = result['context_access']
        access['related_sources'] = links
        ids = [node['node_id'] for node in links['nodes']]
        access['available_node_ids'] = sorted(set(access['available_node_ids']) | set(ids))
        access['leaf_node_ids'] = sorted(set(access['leaf_node_ids']) | set(ids))
        set_remaining(result, maximum)
        from material_packing import material_size
        if (material_size(result) > maximum or payload is not None
                and len(json.dumps(payload(result), ensure_ascii=False).encode()) > MAX_REQUEST_BYTES):
            return unit
        return result

    def read(self, keys, *, raw=False, max_bytes=None):
        if raw:
            # 读取时逐条比对建索引时的来源SHA，拒绝原文在对象内被替换。
            with self.connect() as db:
                hashes = {}
                for key in keys:
                    node = self.load(key, db)
                    if node['kind'] != 'leaf':
                        raise ValueError('原文访问须选择叶节点')
                    eid = node['note']['event_id']
                    if eid not in hashes:
                        hashes[eid] = digest(self.events[eid])
                    if hashes[eid] != node['source_event_sha256']:
                        raise ValueError('原文索引来源内容已变化')
        return super().read(keys, raw=raw, max_bytes=max_bytes)


def build_view(packet, root):
    from hierarchical_reading import compact
    nav = SourceIndex(Path(root)/'context-navigation', packet)
    tree = nav.build()
    view = compact(packet, nav.descriptor(tree['root']), dict(original_packet_sha256=digest(packet),
        navigation_tree_sha256=digest(tree), source_access_mode=MODE, model_first_read=False,
        semantic_acceptance=False, scope=LIMITATION))
    view['context_navigation']['limitation'] = LIMITATION
    atomic(Path(root)/'reading-view.json', view)
    return view
