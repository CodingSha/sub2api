"""首读前记录可证明的新发送下界；未知后续工作不计零、不宣布全流程可行。"""
import json
from pathlib import Path

from runtime import atomic, digest


def preflight(client, reading, root, events, cache, used):
    from bounded_read import source_page
    from pipeline import Paused
    flow = getattr(client, 'flow', None)
    # 调用前已经逐字核对本目录plan；目录键绑定完整计划与阅读合同，不再每轮哈希整份页表。
    identity = dict(version=2, reading_identity=root.name,request_budget_path=str(client.budget.path.resolve()),personal_limit=client.limit,
        global_scope=dict(budget_key=flow.config['budget_key'],limit=flow.config['max_requests']) if flow else None)
    # 公共计划可由不同预算任务复用；观测不能冻结在首个任务的余额/限额上。
    # 旧根级观测保留，既不覆盖也不拿它为当前任务授权。
    path = root/'capacity-preflights'/digest(identity)/'capacity-preflight.json'
    if path.exists():
        prior = json.loads(path.read_text())
        if prior.get('identity') != identity or prior.get('result_sha256') != digest(prior.get('result')):
            raise ValueError('首读容量记录的来源、额度或内容变化，保留原记录待核')
        # 只复用初始观测，不授权任何请求；后续每次发送仍由原任务及全局账簿限制。
        # 已通过观测不反复扫描全缓存；预算不足的观测允许补齐真实缓存后重新核查。
        if prior['result']['state'] != 'insufficient_known_reading_budget':return prior['result']
    global_budget = flow.remaining_requests() if flow is not None else None
    personal_remaining = max(0, client.limit-used)
    available = min(personal_remaining, global_budget['remaining']) if global_budget else personal_remaining
    pages = len(reading['pages']); missing = 0; existing = 0
    inspected = pages > available
    if inspected:
        for index, page_index in enumerate(reading['pages']):
            # 已有局部回执可能含有界修复链；这里只保守计0，不绕过后续原输入/引文校验。
            if (root/(str(index)+'.receipt.json')).exists():
                existing += 1;continue
            page = source_page(page_index, events)
            found = cache.inspect(page, client.reading_payload(page)) if cache is not None else None
            if found is None:missing += 1
            else:existing += 1
    insufficient = inspected and missing > available
    result = dict(state='insufficient_known_reading_budget' if insufficient else 'initial_reading_observation_saved',
        pages=pages, personal_used=used, personal_remaining=personal_remaining, global_budget=global_budget,
        cache_inspected=inspected, known_new_request_lower_bound=missing if inspected else 0,
        existing_receipts_pending_normal_validation=existing if inspected else None,
        base_requests_if_all_pages_missing=pages, whole_pipeline_feasibility=False,
        unestimated_stages=['navigation', 'goal_identification_and_merge', 'evidence_readback',
            'scoring', 'semantic_audit', 'explanation_generation_and_audit', 'repairs_and_retries', 'human_review_and_publish'],
        limitation='这是首读前的时点观测，不保留未来额度、不证明缓存语义有效。已有回执仍须原流程验签/验引文；'
            '格式修复、后续阶段和其他用户消耗均未算成0。观测通过不代表首读或全流程一定能完成。')
    atomic(path, dict(identity=identity, result=result, result_sha256=digest(result)))
    if insufficient:
        raise Paused('needs_review', '首读前已确认至少需要'+str(missing)+'次新请求，当前个人/全局可用额度仅'
            +str(available)+'次；本次预检未发新请求，保留全部来源及既有回执待核，后续阶段另需预算')
    return result


def preflight_navigation(client,packet,reading,root,events,cache,used,navigation_root):
    """仅对可证明的冷导航计新增下界；部分缓存未知不冒充全冷或全热。"""
    from bounded_read import source_page
    from context_navigation import local_identity,MAX_CHILDREN
    from pipeline import Paused
    navigation_root=Path(navigation_root)
    scope=local_identity(packet,reading.get('page_navigation_mode'))
    shared=getattr(client,'navigation_cache_root',None)
    namespace=Path(shared)/digest(scope) if shared is not None else None
    present=lambda path:path is not None and path.exists() and any(path.iterdir())
    cold=not present(navigation_root) and not present(namespace)
    pages=len(reading['pages'])
    semantic_inputs=pages if 'page_navigation_mode' in reading else sum(len(p['fragments']) for p in reading['pages'])
    structural_minimum=max(0,(semantic_inputs-1+MAX_CHILDREN-2)//(MAX_CHILDREN-1))
    navigation_minimum=structural_minimum if cold else 0
    global_budget=client.flow.remaining_requests() if client.flow is not None else None
    personal_remaining=max(0,client.limit-used)
    available=min(personal_remaining,global_budget['remaining']) if global_budget else personal_remaining
    # 与read_all共用真正的计划目录，包含公共reading_plan_root下的原回执。
    local_slots={int(p.name.split('.')[0]) for p in root.glob('*.receipt.json')
        if p.name.split('.')[0].isdigit() and int(p.name.split('.')[0])<pages}
    pending=len(local_slots);missing=0
    inspected=navigation_minimum>0 and pages-pending+navigation_minimum>available
    if inspected:
        for index,page_index in enumerate(reading['pages']):
            if index in local_slots:continue
            page=source_page(page_index,events)
            found=cache.inspect(page,client.reading_payload(page)) if cache is not None else None
            if found is None:missing+=1
            else:pending+=1
    lower=missing+navigation_minimum
    result=dict(version=1,reading_identity=root.name,navigation_scope_sha256=digest(scope),
        pages=pages,semantic_navigation_inputs=semantic_inputs,
        structural_navigation_request_lower_bound=structural_minimum,
        navigation_cache_state='observed_empty' if cold else 'existing_evidence_pending_normal_validation',
        known_new_navigation_request_lower_bound=navigation_minimum,
        cache_inspected=inspected,known_new_reading_request_lower_bound=missing,
        existing_reading_receipts_pending_normal_validation=pending,
        known_new_request_lower_bound=lower,personal_used=used,personal_remaining=personal_remaining,
        global_budget=global_budget,available_requests=available,
        state='insufficient_known_workflow_budget' if lower>available else 'workflow_observation_saved',
        whole_pipeline_feasibility=False,
        unestimated_stages=(['reading'] if not inspected and pages>pending else [])
            +(['navigation_beyond_structural_lower_bound'] if cold else ['navigation'])
            +['goal_identification_and_merge','evidence_readback','scoring','semantic_audit','explanation_generation_and_audit',
            'repairs_and_retries','human_review_and_publish'],
        limitation='只记录本时点可证明的首读与冷导航新增下界，不预留额度。程序叶不计语义输入；'
            '已有导航证据时新增导航量未知，0仅为下界。回执仍须正常验签/验引文，'
            '未估算环节不视为零成本；通过不证明全流程可完成。')
    path=navigation_root.parent/'workflow-capacity.json'
    if path.exists():
        prior=json.loads(path.read_text())
        if prior.get('result_sha256')!=digest(prior.get('result')):
            raise ValueError('工作流容量观测签名变化，保留原记录待核')
    proof=dict(result=result,result_sha256=digest(result))
    if not path.exists() or prior!=proof:atomic(path,proof)
    if lower>available:
        atomic(path.parent/'capacity-blocks'/(digest(result)+'.json'),proof)
        raise Paused('needs_review','首读与冷导航已确认至少需要'+str(lower)+'次新请求，当前可用额度仅'
            +str(available)+'次；本次未发送新请求，归属、评分与复查等仍另需预算')
    return result
