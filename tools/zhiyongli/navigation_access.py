"""分层上下文的受控访问；只展开已展示节点，原文不自动认领为本人。"""
import copy
import json
from pathlib import Path

from bounded_read import raw_size
from context_navigation import INPUT_BYTES
from navigation_contract import DIRECTORY_MODE, INITIAL_DIRECTORY_MODE
from runtime import atomic, digest, moment
from material_packing import material_size

VERSION = 'navigation-access-20261109.1'
UNIT_BYTES = 120_000


class ReadbackCapacity(ValueError):
    """已验明请求的节点超过执行容量；不是来源或访问证明损坏。"""


def freeze(path, value):
    path=Path(path)
    if path.exists() and json.loads(path.read_text())!=value:
        raise ValueError('分层访问的冻结输入、查询或结果发生变化，不能覆盖原记录')
    atomic(path,value)


class Access:
    def __init__(self, navigation, directory, native=None, *, unit_bytes=None, child_directory=True,
                 initial_directory=False, material_encoding=None):
        if unit_bytes is not None and (type(unit_bytes) is not int or not 0<unit_bytes<=UNIT_BYTES):
            raise ValueError('分层访问容量必须在原材料上限内')
        self._unit_bytes=unit_bytes
        self.navigation=navigation;self.root=Path(directory);self.native=native
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.tree=json.loads((navigation.root/'tree.json').read_text())
        leaves=json.loads((navigation.root/'leaves.json').read_text())
        if (self.tree['scope']!=navigation.identity or leaves['scope']!=navigation.scope
                or self.tree['leaves_sha256']!=digest(leaves)):
            raise ValueError('分层访问的树、叶索引与冻结来源不符')
        self.identity=dict(version=VERSION,tree_sha256=digest(self.tree),leaves_sha256=digest(leaves),
            source_scope=navigation.scope,packet_sha256=digest(navigation.packet))
        self.material_encoding=material_encoding
        if material_encoding is not None:
            from material_packing import VERSION as PACKING_VERSION
            if material_encoding!=PACKING_VERSION:raise ValueError('未知材料传输编码')
            self.identity['material_encoding']=material_encoding
        if unit_bytes is not None:self.identity['unit_bytes']=unit_bytes
        # 旧访问记录不回填新目录，不改变其已冻结状态和请求输入。
        prior=json.loads((self.root/'input.json').read_text()) if (self.root/'input.json').exists() else None
        self.directory_mode=prior.get('child_directory_mode') if prior is not None else DIRECTORY_MODE if child_directory else None
        if self.directory_mode not in (None,DIRECTORY_MODE):raise ValueError('未知的子节点目录合同')
        if self.directory_mode is not None:self.identity['child_directory_mode']=self.directory_mode
        # 新入口只为新建访问根启用；已有根缺标志时保持原首次输入及回执身份。
        self.initial_directory_mode=(prior.get('initial_directory_mode') if prior is not None else
            INITIAL_DIRECTORY_MODE if initial_directory and self.directory_mode else None)
        if (self.initial_directory_mode not in (None,INITIAL_DIRECTORY_MODE)
                or self.initial_directory_mode is not None and self.directory_mode is None):
            raise ValueError('未知或缺少子目录合同的初始根目录模式')
        if self.initial_directory_mode is not None:self.identity['initial_directory_mode']=self.initial_directory_mode
        self.leaf_count=len(leaves['node_ids'])
        self.navigation.load(self.tree['root'])
        freeze(self.root/'input.json',self.identity)

    @property
    def unit_bytes(self):
        return UNIT_BYTES if self._unit_bytes is None else min(UNIT_BYTES,self._unit_bytes)

    def remember(self,unit):
        proof=dict(identity=self.identity,unit_sha256=digest(unit))
        freeze(self.root/'states'/(proof['unit_sha256']+'.json'),proof)

    def start(self, unit, *, initial_payload=None):
        packet=self.navigation.packet
        periods=unit.get('periods',[dict(start=unit.get('since'),end=unit.get('until'))])
        if (str(unit.get('user_id'))!=str(packet['user_id']) or len(periods)!=1
                or moment(periods[0]['start'])!=moment(packet['since'])
                or moment(periods[0]['end'])!=moment(packet['until'])):
            raise ValueError('分层访问的人员或评价时窗不同，禁止跨人或将整周导航用于月内片段')
        if unit.get('context_access'):raise ValueError('初始评审对象已含分层访问状态，须从原始冻结对象续接')
        result=copy.deepcopy(unit)
        if self.material_encoding is not None:result['material_encoding']=self.material_encoding
        result['context_access']=dict(version=VERSION,scope=self.navigation.scope,
            root=self.navigation.descriptor(self.tree['root']),available_node_ids=[self.tree['root']],
            leaf_node_ids=[self.tree['root']] if self.navigation.load(self.tree['root'])['kind']=='leaf' else [],
            inspected_node_ids=[],raw_node_ids=[],observations=[],raw_read_fragments=0,
            total_indexed_fragments=self.leaf_count,
            limitation='摘要仅作导航；这里只统计实际补读原文范围，不代表全部原文已用于评分或语义核验通过')
        if self.directory_mode is not None:result['context_access']['child_directory_mode']=self.directory_mode
        if self.initial_directory_mode is not None:result['context_access']['initial_directory_mode']=self.initial_directory_mode
        set_remaining(result,self.unit_bytes)
        if material_size(result)>self.unit_bytes:
            if self.material_encoding is not None:
                raise ReadbackCapacity('无损编码后的完整归属材料仍超容量，沿用分批路径')
            raise ValueError('初始分层评审材料超过评分容量，须继续分层综合，本次不发送模型请求')
        self.remember(result)
        if self.initial_directory_mode is not None and self.navigation.load(self.tree['root'])['kind']=='branch':
            try:
                result=self.expand(result,[dict(node_id=self.tree['root'],mode='inspect')],
                    'initial-root',require_directory=True,initial_payload=initial_payload)
            except ReadbackCapacity:
                # 可选目录放不下时完整回退；未采用的展开不得写查询或新状态证明。
                pass
        if callable(getattr(self.navigation,'initial_links',None)):
            result=self.navigation.initial_links(result,self.unit_bytes,initial_payload)
            self.remember(result)
        return result

    def verify(self,unit):
        if unit.get('material_encoding')!=self.material_encoding:raise ValueError('访问状态的材料编码合同不符')
        proof_path=self.root/'states'/(digest(unit)+'.json')
        proof=json.loads(proof_path.read_text()) if proof_path.exists() else None
        if proof!=dict(identity=self.identity,unit_sha256=digest(unit)):
            raise ValueError('分层访问状态未绑定已提供的实际材料，禁止伪造节点暴露范围')
        access=unit.get('context_access',{})
        if access.get('scope')!=self.navigation.scope:raise ValueError('分层访问范围不同')
        if access.get('child_directory_mode')!=self.directory_mode:raise ValueError('子节点目录执行合同变化')
        if access.get('initial_directory_mode')!=self.initial_directory_mode:raise ValueError('初始根目录执行合同变化')
        directory=access.get('child_directory')
        if directory is not None:
            if (self.directory_mode is None or directory.get('mode')!=self.directory_mode
                    or directory.get('status') not in {'complete','not_provided_capacity'}
                    or not isinstance(directory.get('parent_ids'),list)
                    or not directory['parent_ids'] or len(set(directory['parent_ids']))!=len(directory['parent_ids'])
                    or not set(directory['parent_ids'])<=set(access['inspected_node_ids'])):
                raise ValueError('子节点目录未绑定实际已展开分支')
            expected=self.child_directory(directory['parent_ids']) if directory['status']=='complete' else dict(
                mode=self.directory_mode,status='not_provided_capacity',parent_ids=directory['parent_ids'])
            if directory!=expected:raise ValueError('子节点目录与实际树、描述或顺序不符')

    def child_directory(self, parents):
        """整批完整目录或不提供；逐节点验签，不以部分目录隐藏其他孩子。"""
        result=dict(mode=self.directory_mode,status='complete',parent_ids=parents,branches=[])
        with self.navigation.connect() as db:
            for key in parents:
                node=self.navigation.load(key,db)
                if node['kind']!='branch':raise ValueError('叶节点没有子节点目录')
                children=[]
                result['branches'].append(dict(node_id=key,children=children))
                for child in node['children']:
                    children.append(self.navigation.descriptor(child,db))
                    if raw_size(result)>INPUT_BYTES:return None
        return result

    def add_directory(self, result, values):
        """定位目录可回收，不能让可选提示阻断原本合法的原文访问。"""
        parents=[entry['value']['node_id'] for entry in values
            if entry['mode']=='inspect' and entry['value']['kind']=='branch']
        if not parents:return
        directory=self.child_directory(parents)
        access=result['context_access']
        if directory is not None and raw_size(dict(values=values,child_directory=directory))<=INPUT_BYTES:
            access['child_directory']=directory
            if material_size(result)<=self.unit_bytes:return
        access['child_directory']=dict(mode=self.directory_mode,status='not_provided_capacity',parent_ids=parents)
        if raw_size(dict(values=values,child_directory=access['child_directory']))>INPUT_BYTES or material_size(result)>self.unit_bytes:
            access.pop('child_directory')

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

    def expand(self, unit, queries, stage, *, require_directory=False, initial_payload=None):
        self.check(unit,queries)
        values=[]
        for query in queries:
            value=self.navigation.read([query['node_id']],raw=query['mode']=='raw')['nodes'][0]
            values.append(dict(mode=query['mode'],value=value))
        sizes='；'.join(entry['value']['node_id'][:12]+'='+str(raw_size(entry['value']))+'字节' for entry in values)
        if raw_size(values)>INPUT_BYTES:
            raise ReadbackCapacity('分层补读合计超过单次容量，须减少节点，不截断：单次上限'+str(INPUT_BYTES)+'字节，本次请求'+sizes)
        result=copy.deepcopy(unit);access=result['context_access'];available=set(access['available_node_ids'])
        # 只回收上轮定位目录；原始观察、原文、可访问节点及磁盘历史均保留。
        if self.directory_mode is not None:access.pop('child_directory',None)
        leaves=set(access['leaf_node_ids'])
        for entry in values:
            value=entry['value'];key=value['node_id']
            if entry['mode']=='raw':
                event=self.raw_event(result,value);access['raw_node_ids'].append(key)
                access['observations'].append(dict(node_id=key,mode='raw',event=event,
                    source_event=value['event_id'],source_span=[value['start'],value['end']],source_event_sha256=value['source_event_sha256']))
            else:
                available.update(value.get('children',[]));access['inspected_node_ids'].append(key)
                # 标出已展示节点中的叶节点，模型据此只对叶节点请求原文。
                leaves.update(c for c in value.get('children',[]) if self.navigation.load(c)['kind']=='leaf')
                access['observations'].append(dict(node_id=key,mode='inspect',node=value))
        access.update(available_node_ids=sorted(available),leaf_node_ids=sorted(leaves),raw_read_fragments=len(access['raw_node_ids']))
        if callable(getattr(self.navigation,'compact_access',None)):
            self.navigation.compact_access(result)
        set_remaining(result,self.unit_bytes)
        if material_size(result)>self.unit_bytes:
            raise ReadbackCapacity('分层补读后的完整材料超过评分容量，须继续分层综合，不裁掉反证或原文：补读前剩余约'
                +str(unit['context_access'].get('remaining_bytes','未知'))+'字节，本次请求'+sizes+'；请求更少的原文节点，或基于已提供内容给出判断')
        if self.directory_mode is not None:self.add_directory(result,values)
        if require_directory and access.get('child_directory',{}).get('status')!='complete':
            raise ReadbackCapacity('完整初始根目录无法装入原容量，保留原首次访问状态')
        if initial_payload is not None:
            from runtime import MAX_REQUEST_BYTES
            if len(json.dumps(initial_payload(result),ensure_ascii=False).encode())>MAX_REQUEST_BYTES:
                raise ReadbackCapacity('完整初始目录的实际请求结构超容量，保留原首次访问状态')
        proof=dict(identity=self.identity,previous_unit_sha256=digest(unit),queries=queries,next_unit_sha256=digest(result))
        freeze(self.root/'queries'/(stage+'.json'),proof)
        self.remember(result)
        return result


def set_remaining(unit,maximum=UNIT_BYTES):
    """原文预算可回收临时目录；不把定位提示永久计入原文占用。"""
    access=unit['context_access'];access['remaining_bytes']=0
    view=dict(unit,context_access={k:v for k,v in access.items() if k!='child_directory'})
    access['remaining_bytes']=max(0,maximum-material_size(view))


def query_variants(unit):
    """可请求的节点按模式分开：未展开的分支只能inspect，未读的叶节点只能raw；已提供的不再出现。"""
    from review_schema import obj, enum
    access=unit['context_access'];leaves=set(access.get('leaf_node_ids',[]))
    inspect=[n for n in access['available_node_ids'] if n not in leaves and n not in access['inspected_node_ids']]
    raw=[n for n in access['available_node_ids'] if n in leaves and n not in access['raw_node_ids']]
    return [obj(dict(node_id=enum(*ids),mode=enum(mode))) for mode,ids in (('inspect',inspect),('raw',raw)) if ids]


def query_schema(unit):
    from review_schema import obj, array
    variants=query_variants(unit)
    if not variants:
        return dict(array(obj(dict(node_id=dict(type='string'),mode=dict(type='string')))),maxItems=0)
    return dict(array(variants[0] if len(variants)==1 else {'anyOf':variants}),maxItems=24)
