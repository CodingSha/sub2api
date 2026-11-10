"""跨批目标的有界全对比较；原句不裁切，冲突不靠传递关系隐藏。"""
import copy
import json
from pathlib import Path

from bounded_read import raw_size
from runtime import atomic, digest
from review_schema import obj, array, enum

INPUT_BYTES = 85_000


def plan(data, max_bytes=INPUT_BYTES):
    """分组只是执行单位；每组内及每对组均比较，不以执行切口推断任务边界。"""
    base = {k: copy.deepcopy(v) for k, v in data.items() if k != 'local_goals'}
    goals = data['local_goals']
    ids = [g['local_goal_id'] for g in goals]
    if len(ids) != len(set(ids)):
        raise ValueError('全局归并局部目标标识重复')
    # 两组加同一份完整导航须仍可装配；大目标可单列，其他组按实际剩余容量装配。
    # 不用“单个目标必须小于一半预算”的假限制拒绝本来可执行的一大一小比较。
    base_bytes = raw_size(dict(base, local_goals=[]))
    if base_bytes >= max_bytes:
        raise ValueError('目标归并的导航本身超容量，须先完成分层导航访问')
    if not goals:return base, []
    if raw_size(data) <= max_bytes:return base, [copy.deepcopy(goals)]
    sizes = [raw_size([g]) for g in goals]
    largest = max(sizes);half = (max_bytes - base_bytes + 3) // 2
    allowance = min(half,max_bytes - base_bytes - largest + 3)
    groups = [];current = []
    for goal,size in zip(goals,sizes):
        if size > half:
            if base_bytes + size - 2 > max_bytes:
                raise ValueError('单个目标的完整表达超出比较容量，须分层核对，不裁掉原句')
            if current:groups.append(current);current=[]
            groups.append([copy.deepcopy(goal)]);continue
        if size > allowance:
            raise ValueError('两目标的完整表达无法共同装配，须分层核对，不裁掉原句')
        if current and raw_size(current + [goal]) > allowance:
            groups.append(current);current = []
        current.append(copy.deepcopy(goal))
    if current:groups.append(current)
    largest_groups = sorted((raw_size(g) for g in groups),reverse=True)[:2]
    if len(largest_groups)==2 and base_bytes+sum(largest_groups)-3 > max_bytes:
        raise ValueError('两目标组的完整表达无法共同装配，不发送部分比较')
    return base, groups


def comparison_schema(data):
    ids = [g['local_goal_id'] for g in data['local_goals']]
    contexts = sorted({eid for g in data['local_goals'] for eid in g['goal'].get('context_event_ids', [])})
    text = dict(type='string')
    context_refs = array(enum(*contexts)) if contexts else dict(type='array', items=text, maxItems=0)
    return obj(dict(goals=array(obj(dict(goal_id=text, title=text, purpose=text,
        boundary=enum('confirmed','unresolved'), boundary_reason=text,
        local_goal_ids=array(enum(*ids)), context_event_ids=context_refs)))))


def checked(data, value):
    expected = {g['local_goal_id'] for g in data['local_goals']}
    contexts = {eid for g in data['local_goals'] for eid in g['goal'].get('context_event_ids', [])}
    seen = set();goal_ids = set()
    if not isinstance(value.get('goals'), list):raise ValueError('比较结果缺目标分组')
    for goal in value['goals']:
        ids = goal.get('local_goal_ids')
        if (not isinstance(ids, list) or not ids or any(not isinstance(i,str) for i in ids)
                or len(ids) != len(set(ids)) or not set(ids) <= expected or seen & set(ids)):
            raise ValueError('目标比较结果遗漏、重复或越界')
        seen.update(ids)
        if (not isinstance(goal.get('goal_id'),str) or not goal['goal_id'].strip() or goal['goal_id'] in goal_ids
                or any(not isinstance(goal.get(k),str) or not goal[k].strip() for k in ('title','purpose','boundary_reason'))
                or goal.get('boundary') not in {'confirmed','unresolved'}):
            raise ValueError('目标比较缺语义边界依据或标识重复')
        goal_ids.add(goal['goal_id'])
        refs = goal.get('context_event_ids')
        if not isinstance(refs,list) or any(not isinstance(e,str) or e not in contexts for e in refs):
            raise ValueError('目标比较背景引用越出本次已提供来源')
    if seen != expected:raise ValueError('目标比较没有全覆盖实际局部目标')
    return value['goals']


def components(data, decisions):
    """只把明确合并关系用于连通；最终状态还须检查全部分离及未决判断。"""
    parent = {g['local_goal_id']: g['local_goal_id'] for g in data['local_goals']}
    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]];key = parent[key]
        return key
    for result in decisions:
        for goal in result:
            if goal['boundary'] == 'confirmed':
                first,*rest = goal['local_goal_ids']
                for key in rest:parent[find(key)] = find(first)
    groups = {}
    for key in parent:groups.setdefault(find(key),[]).append(key)
    return list(groups.values())


def observations(data, decisions):
    """按成员索引一次分发比较结论，避免每个目标都重扫所有比较及所有其他目标。"""
    groups = components(data, decisions)
    owners = {key:i for i,members in enumerate(groups) for key in members}
    result = [dict(members=members,proposals=[],conflicts=set()) for members in groups]
    for decision in decisions:
        matched = {}
        for goal in decision:
            counts = {}
            for key in goal['local_goal_ids']:counts[owners[key]] = counts.get(owners[key],0)+1
            for owner,count in counts.items():
                result[owner]['proposals'].append((count,goal))
                matched[owner] = matched.get(owner,0)+1
                if goal['boundary']=='unresolved':result[owner]['conflicts'].add('某次比较仍有目标边界未决')
        for owner,count in matched.items():
            if count>1:result[owner]['conflicts'].add('某次完整比较仍将本组成员判断为不同目标')
    return result


def combine(data, decisions):
    output = [];unresolved = {g['local_goal_id'] for g in data['local_goals'] if g['goal']['boundary']=='unresolved'}
    for observation in observations(data, decisions):
        members = observation['members'];conflicts = observation['conflicts'];proposals = observation['proposals']
        # 标题和目的仅作导航；实际本人原句仍由原识别图完整装配，不据此给分。
        chosen = max(proposals,key=lambda pair:pair[0])[1]
        goal = {k:copy.deepcopy(chosen[k]) for k in ('title','purpose','boundary_reason')}
        if len(members)>len(chosen['local_goal_ids']):
            goal['boundary_reason']+='；名称与目的仅为局部导航，须结合完整保留的全组原句还原有效要求'
        if unresolved.intersection(members):conflicts.add('原局部边界未决尚未逐条解决')
        goal.update(goal_id='global-'+digest(sorted(members))[:24],local_goal_ids=sorted(members),
            boundary='unresolved' if conflicts else 'confirmed',
            context_event_ids=sorted({eid for _,g in proposals for eid in g.get('context_event_ids',[])}))
        if conflicts:goal['boundary_reason']+='；全局比较保留：'+'；'.join(sorted(set(conflicts)))
        output.append(goal)
    merged = dict(goals=output)
    checked(data,merged)
    return merged


def reconcile(data, directory, client, receive, progress=lambda *args:None, max_bytes=INPUT_BYTES):
    base, groups = plan(data, max_bytes)
    root = Path(directory);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    stamp = dict(input_sha256=digest(data), max_bytes=max_bytes, groups=[digest(g) for g in groups],
        comparisons=len(groups)*(len(groups)+1)//2)
    path = root/'plan.json'
    if path.exists() and json.loads(path.read_text()) != stamp:
        raise ValueError('全局目标比较来源或容量合同变化，原回执保留，须显式修订')
    atomic(path, stamp)
    decisions = [];completed = 0
    for left in range(len(groups)):
        for right in range(left, len(groups)):
            pair = groups[left] + (groups[right] if left != right else [])
            request = dict(base,local_goals=pair)
            if raw_size(request) > max_bytes:raise ValueError('目标比较装配超过容量，不发送')
            value = receive(client.merge_goals(request, comparison_schema(request), root/(digest(request)+'.receipt.json')))
            decisions.append(checked(request,value))
            completed += 1
            progress('identifying',completed,stamp['comparisons'],'有界比较跨批目标，完整表达及边界冲突均保留')
    merged = combine(data, decisions)
    atomic(root/'result.json',dict(value=merged,input_sha256=digest(data),
        comparisons=stamp['comparisons'],limitation='全对比较只证明执行覆盖；语义有效性仍须完整真实材料验收，不按任务数计分'))
    return merged
