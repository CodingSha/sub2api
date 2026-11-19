"""每日事实与周期评审复用：逐动作验签、逐来源核对，新增材料继续处理，修订不沿用旧判断。"""
import copy
import json
from pathlib import Path
import tempfile

from navigation_access import freeze
from runtime import digest

VERSION='session-facts-20261118.1'


def complete(state,ready):
    """日常任务只封口事实，不生成周分或伪造审核通过。"""
    state.native(ready['source_ids'])
    value=dict(version=VERSION,packet_sha256=digest(state.packet),
        materials_sha256=digest(state.materials),tasks_sha256=digest(state.tasks),
        sources_sha256=digest(state.sources),summary_sha256=digest(state.summary),ready=ready)
    freeze(state.root/'facts-completed.json',value)
    return dict(state='facts_prepared',materials=len(state.materials),tasks=len(state.tasks),sources=len(state.sources))


def inherit(state,source,*,ancestors=()):
    """复用前一日已核事实；来源有修订则重新理解，不把过期事实当成本期证据。"""
    from session_evidence import SessionEvidence
    from session_review import EvidenceState,seed_state
    source=Path(source).resolve()
    if source in ancestors or source==state.root.resolve() or len(ancestors)>=8:
        raise ValueError('每日事实继承有循环或超过本周有界天数')
    packet=json.loads((source/'packet.json').read_text())
    if any(packet.get(k)!=state.packet.get(k) for k in ('user_id','user_email','since','until','workday_scope')):
        raise ValueError('每日事实不得跨人员、日历或周期复用')
    completed=source/'facts-completed.json'
    if not completed.exists():raise ValueError('每日事实尚未封口，不继承局部或未知结果')
    stamp=json.loads(completed.read_text())
    if stamp.get('empty'):
        if packet['events'] or packet['issues'] or stamp!=dict(version=VERSION,packet_sha256=digest(packet),empty=True):
            raise ValueError('空来源每日事实与原件不一致')
        return dict(reused=True,saved_materials=0,tasks=0,sources=0,pending=state.pending()['remaining'])
    with tempfile.TemporaryDirectory(prefix='zhiyongli-fact-proof-') as temporary:
        old_store=SessionEvidence(source/'session-evidence')
        try:
            old_store.verify()
            old=EvidenceState(packet,old_store,temporary)
            seed_state(old,source,allow_facts=True,fact_ancestors=ancestors+(source,))
            stamp=json.loads(completed.read_text())
            expected=dict(version=VERSION,packet_sha256=digest(packet),materials_sha256=digest(old.materials),
                tasks_sha256=digest(old.tasks),sources_sha256=digest(old.sources),summary_sha256=digest(old.summary),ready=stamp['ready'])
            if stamp!=expected:raise ValueError('每日事实封口与原始模型动作不一致')
            origin_to_key={row['origin']:key for key,row in state.rows.items()}
            dependencies={piece['event_key'] for proof in old.reads.values()
                for piece in proof.get('items',[proof]) if piece.get('event_key') in old.rows}
            changed=[];mapping={}
            for key in dependencies:
                origin=old.rows[key]['origin'];current=origin_to_key.get(origin)
                if current is None or digest(old.events[origin])!=digest(state.events[origin]):changed.append(origin)
                else:mapping[key]=current
            if changed:
                freeze(state.root/'facts-invalidated.json',dict(source=str(source),source_facts_sha256=digest(stamp),
                    changed_origin_events=sorted(changed),reason='已读依赖原件修订或不在当前范围；原事实保留，本次重新理解，不继承旧结论'))
                return dict(reused=False,changed_events=len(changed))
            def remap(value):
                if isinstance(value,list):return [remap(v) for v in value]
                if not isinstance(value,dict):return value
                return {k:mapping.get(v,v) if k=='event_key' and isinstance(v,str) else remap(v) for k,v in value.items()}
            reads=remap(old.reads)
            for proof in reads.values():
                for piece in proof.get('items',[proof]):
                    if piece.get('event_key') not in state.rows:continue
                    actual=state.store.read_event(piece['event_key'],piece['start'],piece['end']-piece['start'])
                    if actual!=piece:raise ValueError('每日事实的原文区间与当前来源不一致')
            state.reads.update(reads)
            result=state.save(dict(materials=remap(list(old.materials.values())),tasks=copy.deepcopy(list(old.tasks.values())),
                sources=remap(list(old.sources.values())),summary=old.summary))
            proof=json.loads((Path(temporary)/'seed-input.json').read_text())
            freeze(state.root/'inherited-facts.json',dict(version=VERSION,source=str(source),
                source_packet_sha256=digest(packet),source_facts_sha256=digest(stamp),
                packet_sha256=digest(state.packet),files=proof['files'],materials_sha256=digest(state.materials),
                tasks_sha256=digest(state.tasks),sources_sha256=digest(state.sources)))
            return dict(reused=True,unchanged_events=digest(packet['events'])==digest(state.packet['events']),
                ready=copy.deepcopy(stamp['ready']),**result)
        finally:old_store.close()
