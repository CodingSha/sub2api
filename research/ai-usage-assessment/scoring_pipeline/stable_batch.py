"""v14持久评审入口：独立版本、不激活全局补丁、不使用子Agent。"""
import argparse
import collections
import copy
import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
from pathlib import Path

from . import dsh_v11 as source
from .batch_review import RunLock, packet as source_packet, context_rows
from .batch_client import ModelError, atomic_json, configured_model, digest
from .common import require, moment
from .stable_client import JSONClient
from .stable_review import VERSION, RUBRIC, INSTRUCTIONS, check
from . import stable_review as default_review

EXECUTION_VERSION='observation-batch-20261103.10'
MAX_STEPS=8


def now():return datetime.now(timezone.utc).isoformat()


def module_hashes():
    paths={str(Path(m.__file__).resolve()) for n,m in sys.modules.items()
           if n.startswith(__package__+'.') and '.test_' not in n and getattr(m,'__file__','').endswith('.py')}
    # -m运行时本文件叫__main__，也必须冻结，不能依赖模块名碰巧被导入。
    paths.add(str(Path(__file__).resolve()))
    main_path=getattr(sys.modules.get('__main__'),'__file__',None)
    if main_path and Path(main_path).resolve().parent==Path(__file__).resolve().parent:
        paths.add(str(Path(main_path).resolve()))
    return {p:source.sha(p) for p in sorted(paths)}


def verified_dataset(path):
    data=source.read(path)
    require(data['policy_version']=='pmje-challenge-20261102.13.2','须固定v13最终来源索引')
    for ref in list(data['source_groups'].values())+data.get('frozen_files',[]):
        require(source.sha(ref['path'])==ref['sha256'],'冻结来源SHA变化')
    return data


def initialize(dataset,run,keys=None,purpose='development',replicate='original',contract=None):
    reviewer=contract or default_review
    data=verified_dataset(dataset);run=Path(run).resolve()
    require(not run.exists(),'禁止覆盖旧运行')
    keys=list(data['tasks']) if keys is None else keys
    require(isinstance(keys,list) and keys and len(keys)==len(set(keys)) and set(keys)<=set(data['tasks']),'任务选择错误；显式空列表不能扩大为全量')
    model=configured_model();run.mkdir(parents=True)
    modules=module_hashes();snapshot=run/'frozen-code';snapshot.mkdir()
    require(len({Path(p).name for p in modules})==len(modules),'冻结模块文件名冲突')
    for path,sha in modules.items():
        dest=snapshot/Path(path).name;dest.write_bytes(Path(path).read_bytes())
        require(source.sha(dest)==sha,'初始化期间源码变化')
    atomic_json(snapshot/'module-paths.json',modules)
    atomic_json(run/'manifest.json',dict(execution_version=EXECUTION_VERSION,version=reviewer.VERSION,run_id=uuid.uuid4().hex,
        created_at=now(),dataset=str(Path(dataset).resolve()),dataset_sha256=source.sha(dataset),model=model,
        purpose=purpose,replicate=replicate,keys=keys,instructions_sha256=digest(reviewer.INSTRUCTIONS),rubric_sha256=digest(reviewer.RUBRIC),
        modules=modules,production_ready=False,independent_human_review=False))
    atomic_json(run/'queue.json',{k:dict(status='restricted' if data['tasks'][k]['local_only'] else 'pending',calls=0,history=[]) for k in keys})
    return status(run)


def open_run(run,contract=None):
    reviewer=contract or default_review
    m=source.read(run/'manifest.json')
    require(m['execution_version']==EXECUTION_VERSION and m['version']==reviewer.VERSION,'执行/语义版本变化')
    require(source.sha(m['dataset'])==m['dataset_sha256'],'索引变化')
    require(digest(reviewer.INSTRUCTIONS)==m['instructions_sha256'] and digest(reviewer.RUBRIC)==m['rubric_sha256'],'合同变化')
    for path,sha in m['modules'].items():require(source.sha(path)==sha,'冻结代码变化：'+Path(path).name)
    return verified_dataset(m['dataset']),m


def packet(data,key,period):
    require(not data['tasks'][key]['local_only'],'受限禁止装配发送')
    original=source.task(data,key)
    withheld={e['event'] for e in original['events'] if e.get('actor_class')=='assistant' and e.get('response_timestamp_unknown') and moment(e['at'])<moment(period['end'])}
    before={e['event'] for e in original['events'] if e['event'] not in withheld and moment(e['at'])<moment(period['end'])}
    narrowed=dict(data,tasks=dict(data['tasks']))
    narrowed['tasks'][key]=dict(data['tasks'][key],periods=[period],person_events=[e for e in original['person_events'] if e in before])
    unit,pack,allowed=source_packet(narrowed,key)
    allowed={k:e for k,e in allowed.items() if k in before}
    unit['events']=list(allowed.values())
    unit['periods']=[period]
    current={e for e in unit['person_events'] if e in allowed and moment(allowed[e]['at'])>=moment(period['start'])}
    unit['person_events']=[e for e in unit['person_events'] if e in current]
    pack['policy_version']=VERSION
    pack.pop('user_id',None)  # 人名不是判分证据；key只用于追溯。
    for field in ('title','purpose','boundary_reason'):
        pack.pop(field,None)  # 月末形成的索引说明也可能泄漏未来目标。
    pack['person_events']=unit['person_events']
    pack['withheld_context']={'response_time_unknown_count':len(withheld),'reason':'没有独立回复时间，不能用请求时间冒充回复已在本期完成；不提供正文或允许补读。'}
    raw=pack['raw_material']
    prior=[r for r in raw['person_events'] if r['event'] in allowed and r['event'] not in current]
    raw['person_events']=[r for r in raw['person_events'] if r['event'] in current]
    raw['prior_person_context']=prior
    # 只提供紧凑定位预览；保留所有允许事件，全文按需补读，不按内容好坏筛选。
    for row in context_rows(pack):
        if row.get('linkage')=='source_group_only' or row['event'] not in allowed:continue
        e=allowed[row['event']]
        row['preview']=e['text'][:200];row['preview_only']=len(e['text'])>200
        row['kind']=row.get('kind') or e.get('actor_class')
        row['actor_class']=e.get('actor_class')
        row['assessment_role']='current_person_source_wrapper' if row['event'] in current else 'prior_person_context' if row['event'] in original['person_events'] else 'other_task_context'
        row['source_limits']={k:True for k in ('truncated','source_snapshot_conflict','response_ambiguous') if e.get(k)}
        raw.setdefault('linked_context',[]).append(row)
    raw.pop('context_index',None);raw.pop('context_columns',None)
    raw['other_context_index']=[r for r in raw['other_context_index'] if r[0] in allowed]
    return unit,pack,allowed


def evaluate_one(data,key,run,manifest,client,period,contract=None):
    reviewer=contract or default_review
    unit,pack,allowed=packet(data,key,period)
    pack['policy_version']=reviewer.VERSION
    full=set();visible=collections.defaultdict(list);traces=[];correction=None;repairs=0
    for r in pack['raw_material']['person_events']:visible[r['event']].append(r['text'])
    for r in pack['raw_material']['prior_person_context']:visible[r['event']].append(r['text'])
    for r in pack['raw_material'].get('linked_context',[]):visible[r['event']].append(r['preview'])
    # 已完整提供的短事件不必为同一全文核对再请求一次。
    full.update(eid for eid,texts in visible.items() if eid in allowed and allowed[eid]['text'] in texts)
    initially_provided=set(full)
    for step in range(MAX_STEPS):
        if (run/'STOP').exists():raise ModelError('stopped')
        # 初始材料中已完整出现的正文不再复制一份；新增全文及原始校验来源仍保留。
        payload=dict(rubric=reviewer.RUBRIC,task=pack,full_context=[allowed[e] for e in sorted(full-initially_provided)],correction=correction,
                     replicate=manifest['replicate'])
        require(len(json.dumps(payload,ensure_ascii=False))<=1_200_000,'input_budget_exceeded')
        record=client.call(reviewer.INSTRUCTIONS,payload,run/'calls'/source.keyhash(key),manifest['run_id'])
        traces.append({k:record.get(k) for k in ('request_sha256','response_sha256','response_id','model')})
        try:
            value=json.loads(record['text']);needs=value.get('needs_events',[])
            require(isinstance(needs,list) and all(isinstance(e,str) for e in needs),'补读须事件ID列表')
            require(set(needs)<=set(allowed),'补读跨来源/未来/受限')
            if needs:
                require(set(needs)-full,'重复补读无新增')
                full.update(needs);correction='所需事件全文已补齐，请重新返回完整判断。';continue
            unread=set()
            for r in value.get('evidence',[]):
                require(r.get('event') in allowed,'引文跨来源/未来')
                if r['event'] not in full and not any(r.get('quote','') in s for s in visible[r['event']]):unread.add(r['event'])
            for part in value.get('period_reviews',[]):
                for c in part.get('cells',[]):
                    for obj in (c,c.get('quality',{})):
                        unread.update(obj.get('missing',{}).get('checked_events',[]))
            require(unread<=set(allowed),'缺失核对跨来源/未来')
            unread-=full
            if unread:
                full.update(unread);correction='引用或缺失核对所涉正文已补齐，重新判断；不要把预览截短当原始缺失。';continue
            rows=reviewer.check(unit,value)
            return value,rows,dict(calls=traces,full_context_events=sorted(full),same_model_self_check=True,independent_human_review=False)
        except (ValueError,KeyError,TypeError,AttributeError) as ex:
            repairs+=1
            if repairs>2:raise ModelError('contract_failed') from None
            correction=dict(contract_error=str(ex)[:600],previous_output=record['text'],
                            instruction='只按固定合同重新判断，不为通过校验改写原文或制造事实。')
    raise ModelError('context_or_repair_steps_exhausted')


def assemble(key,parts,contract=None):
    """仅合并显式期间结论和重命名引文ID，不填补或重判任何语义字段。"""
    reviewer=contract or default_review
    value=dict(version=reviewer.VERSION,key=key,evidence=[],period_reviews=[],needs_events=[])
    for part in parts:
        original=part['value'];pid=part['period_id'];ids={e['id']:pid+'|'+e['id'] for e in original['evidence']}
        def rename(obj):
            if isinstance(obj,list):return [rename(x) for x in obj]
            if not isinstance(obj,dict):return obj
            return {k:[ids[x] for x in v] if k=='refs' or k.endswith('_refs') else rename(v) for k,v in obj.items()}
        value['evidence'] += [dict(e,id=ids[e['id']]) for e in original['evidence']]
        value['period_reviews'] += rename(original['period_reviews'])
    return value


def evaluate(data,key,run,manifest,client,contract=None):
    reviewer=contract or default_review
    parts=[];traces=[];refs=[]
    for period in data['tasks'][key]['periods']:
        path=run/'parts'/source.keyhash(key)/(period['period_id']+'.json')
        if path.exists():
            part=source.read(path);require(part['run_id']==manifest['run_id'],'期间属于其他运行')
            u,_,_=packet(data,key,period);reviewer.check(u,part['value'])
            verify_model_calls(run,key,part['trace']['calls'],part['value'])
        else:
            value,_,trace=evaluate_one(data,key,run,manifest,client,period,contract=contract)
            part=dict(run_id=manifest['run_id'],period_id=period['period_id'],value=value,trace=trace)
            atomic_json(path,part)
        parts.append(part);traces+=part['trace']['calls'];refs.append(dict(path=str(path.resolve()),sha256=source.sha(path),period_id=period['period_id']))
    value=assemble(key,parts,contract=contract);rows=reviewer.check(source.task(data,key),value)
    return value,rows,dict(calls=traces,parts=refs,assembly='program_period_assembly',same_model_self_check=True,independent_human_review=False)


def verify_model_calls(run,key,calls,value):
    require(calls,'缺实际模型调用')
    for call in calls:
        raw=source.read(run/'calls'/source.keyhash(key)/(call['request_sha256']+'.json'))
        require(raw['request_sha256']==call['request_sha256'] and digest(raw['response'])==call['response_sha256']==raw['response_sha256'],'模型回执SHA不符')
    require(json.loads(raw['text'])==value,'结果不是末次模型原判')


def freeze(data,key,run,m,value,trace,contract=None):
    reviewer=contract or default_review
    require(not data['tasks'][key]['local_only'],'受限禁止冻结普通结果')
    reviewer.check(source.task(data,key),value)
    dest=run/'reviews'/(source.keyhash(key)+'.json');require(not dest.exists(),'禁止覆盖成功原判')
    refs=[data['source_groups'][g] for g in data['tasks'][key]['source_groups']]
    for r in refs:require(source.sha(r['path'])==r['sha256'],'评审期间来源变化')
    receipt=dict(run_id=m['run_id'],version=reviewer.VERSION,execution_version=EXECUTION_VERSION,key=key,
        recorded_at=now(),value_sha256=digest(value),source_refs=refs,model=m['model'],trace=trace,
        semantic_acceptance='pending',independent_human_review=False)
    rp=run/'receipts'/(source.keyhash(key)+'.json');atomic_json(rp,receipt)
    atomic_json(dest,dict(value=value,receipt=dict(path=str(rp.resolve()),sha256=source.sha(rp))))


def inventory(data,run,contract=None):
    reviewer=contract or default_review
    result={};manifest=source.read(run/'manifest.json')
    for path in sorted((run/'reviews').glob('*.json')):
        wrapped=source.read(path);v=wrapped['value'];key=v['key']
        require(key in data['tasks'] and key not in result,'重复/未知任务')
        require(not data['tasks'][key]['local_only'],'受限误入普通结果')
        r=wrapped['receipt'];require(source.sha(r['path'])==r['sha256'],'回执SHA不符')
        receipt=source.read(r['path']);require(receipt['value_sha256']==digest(v) and receipt['key']==key,'结果与回执不符')
        require(receipt['run_id']==manifest['run_id'],'回执属于其他运行')
        parts=[]
        for ref in receipt['trace']['parts']:
            require(source.sha(ref['path'])==ref['sha256'],'期间结果变化')
            part=source.read(ref['path']);require(part['run_id']==manifest['run_id'],'期间属于其他运行')
            verify_model_calls(run,key,part['trace']['calls'],part['value']);parts.append(part)
        require(assemble(key,parts,contract=contract)==v,'整任务结果与显式期间装配不一致')
        result[key]=dict(value=v,rows=reviewer.check(source.task(data,key),v),path=str(path.resolve()))
    return result


def status(run):
    run=Path(run);q=source.read(run/'queue.json')
    return dict(run=str(run),total=len(q),counts=dict(collections.Counter(e['status'] for e in q.values())),
        errors=[dict(key=k,error=e.get('error'),retryable=e.get('retryable',False)) for k,e in q.items() if e['status'] in {'failed','policy_blocked'}])


def work(run,workers=4,limit=None,client=None,contract=None):
    run=Path(run);require(1<=workers<=8,'workers须1—8')
    with RunLock(run):
        data,m=open_run(run,contract=contract);q=source.read(run/'queue.json');good=inventory(data,run,contract=contract)
        for k,e in q.items():
            if k in good:e['status']='completed'
            elif e['status']=='completed':raise ValueError('成功结果丢失')
            elif e['status']=='running':e['status']='pending'
        atomic_json(run/'queue.json',q)
        keys=[k for k,e in q.items() if e['status']=='pending']
        if limit is not None:require(limit>0,'limit须正数');keys=keys[:limit]
        client=client or JSONClient(m['model']);todo=iter(keys);active={}
        def submit(pool):
            if (run/'STOP').exists():return False
            try:key=next(todo)
            except StopIteration:return False
            q[key].update(status='running',started_at=now());atomic_json(run/'queue.json',q)
            active[pool.submit(evaluate,data,key,run,m,client,contract=contract)]=key;return True
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for _ in range(workers):submit(pool)
            while active:
                done,_=wait(active,timeout=30,return_when=FIRST_COMPLETED)
                for future in done:
                    key=active.pop(future);e=q[key]
                    try:
                        value,_,trace=future.result();freeze(data,key,run,m,value,trace,contract=contract)
                        e.update(status='completed',calls=len(trace['calls']),error=None,retryable=False)
                    except ModelError as ex:
                        e.update(status='pending' if ex.code=='stopped' else 'policy_blocked' if ex.code in {'policy_blocked','model_refusal','response_cyber_policy'} else 'failed',error=ex.code,retryable=ex.retryable)
                        if ex.code.startswith(('http_401','http_403','configuration','credentials','model_configuration')):(run/'STOP').touch()
                    except Exception as ex:
                        e.update(status='failed',error=type(ex).__name__+': '+str(ex)[:400],retryable=False)
                    e['finished_at']=now();e['history'].append({k:e.get(k) for k in ('status','error','retryable','started_at','finished_at')})
                    atomic_json(run/'queue.json',q)
                    print(json.dumps(dict(at=now(),key=key,status=e['status'],counts=status(run)['counts']),ensure_ascii=False),flush=True)
                    submit(pool)
    return status(run)


def retry_failed(run,contract=None):
    with RunLock(run):
        open_run(run,contract=contract);q=source.read(run/'queue.json');keys=[]
        for k,e in q.items():
            if e['status']=='failed' and e.get('retryable'):e['status']='pending';keys.append(k)
        atomic_json(run/'queue.json',q)
    return dict(requeued=keys)


def main(contract=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',required=True);sub=p.add_subparsers(dest='command',required=True)
    s=sub.add_parser('init');s.add_argument('--dataset',required=True);s.add_argument('--keys');s.add_argument('--purpose',default='development');s.add_argument('--replicate',default='original')
    s=sub.add_parser('run');s.add_argument('--workers',type=int,default=4);s.add_argument('--limit',type=int)
    for name in ('status','stop','resume','retry-failed','verify'):sub.add_parser(name)
    a=p.parse_args();run=Path(a.run).resolve()
    if a.command=='init':out=initialize(a.dataset,run,source.read(a.keys) if a.keys else None,a.purpose,a.replicate,contract=contract)
    elif a.command=='run':out=work(run,a.workers,a.limit,contract=contract)
    elif a.command=='status':out=status(run)
    elif a.command=='retry-failed':out=retry_failed(run,contract=contract)
    elif a.command=='stop':require((run/'manifest.json').exists(),'先初始化');(run/'STOP').touch();out={'stop_requested':True}
    elif a.command=='resume':
        with RunLock(run):open_run(run,contract=contract);(run/'STOP').unlink(missing_ok=True)
        out={'resumed':True}
    else:
        data,m=open_run(run,contract=contract);good=inventory(data,run,contract=contract);require(set(good)<=set(m['keys']),'结果越出队列')
        out=dict(valid_results=len(good),rows=sum(len(v['rows']) for v in good.values()),**status(run))
    print(json.dumps(out,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
