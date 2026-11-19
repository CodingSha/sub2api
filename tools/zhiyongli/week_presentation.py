"""把另次复查通过的整周依据确定性附入正式报告；不修改评分或发布。"""
import copy
from pathlib import Path

from explanation_stage import enrich, verify
from pipeline import Paused
from runtime import digest
from shared_weeks import read_ref, reference
from task_evidence import VERSION as TASK_VERSION, sha
from task_interpretation import freeze
from week_basis import ITEMS

VERSION = 'checked-week-presentation-20261116.1'


def build(bundle, data, spec):
    """只消费已核说明对象，不制造任务关联、邻档事实或新的评分。"""
    result = copy.deepcopy(bundle)
    if len(result['reports']) != 1:
        raise ValueError('正式周说明只允许一个已核人员周期')
    report = result['reports'][0]
    if report['period_type'] != 'week' or report.get('task_review') is not None:
        raise ValueError('正式周说明周期不符或已有解读，禁止覆盖')
    formal = data['formal_task_keys']
    expected = {k for row in report['eligibility']['observations'] for k in row['task_keys']}
    if not formal or len(formal) != len(set(formal)) or set(formal) != expected:
        raise ValueError('整周说明正式任务范围与已核资格不一致')
    if set(spec['items']) != set(ITEMS) or any(v['verdict'] != 'consistent' for v in spec['items'].values()):
        raise Paused('needs_review', '完整说明仍要求重评或保留未决，原评分及说明回执保留，不接入候选')
    case_data, case_spec = data['case'], spec['case']
    key = case_spec['task_key']
    if key != case_data['task_key'] or key not in formal:
        raise ValueError('代表案例不属于本次已核正式任务')
    record_ids = data['case_record_ids']
    records = [r for r in data['records'] if r['id'] in set(record_ids)]
    if len(records) != len(record_ids) or {r['id'] for r in records} != set(record_ids):
        raise ValueError('代表案例全部记录缺失或重复，禁止截断')
    case = dict(id=sha(key), task_key=key, title=case_data['title'], goal=case_data['goal'],
        outcome=case_spec['outcome'], context_status=case_data['context_status'], record_count=len(records),
        records=records, claims=case_spec['claims'],
        limitations=list(dict.fromkeys(case_data['limitations']+case_spec['limitations'])))
    judgments = []
    for code in ITEMS:
        item = spec['items'][code]
        tasks = [k for k in item['source_keys'] if k in expected]
        if not tasks:
            raise Paused('needs_review', '分项说明仅关联局部表达，当前正式任务合同不能承载，不补造正式任务关联')
        linked = key in tasks and any(code in claim['item_codes'] for claim in case['claims'])
        judgments.append(dict(item_code=code, reason=item['basis']+'\n邻档判断：'+item['higher_band'],
            task_keys=tasks, case_ids=[case['id']] if linked else [], limitations=item['limitations']))
    report['task_review'] = dict(version=TASK_VERSION, status='week_checked', task_keys=formal,
        checked_task_keys=list(formal), cases=[case], week_judgments=judgments,
        scope_note='核查范围为本报告标明的评价周期及工作日，覆盖本次全部已保存可展示关联记录与十二项依据；'
            '代表案例不等于全部任务，模型生成并经另次复查，未经独立真人校准。')
    unchanged = copy.deepcopy(result); unchanged['reports'][0].pop('task_review')
    if digest(unchanged) != digest(bundle):
        raise ValueError('附加说明改变原评分包，禁止导入')
    return result


def complete(entry_ref, root, client, progress=lambda *args: None):
    """必须在导入意图冻结前执行；原JobClient继续管理全部新增请求。"""
    root = Path(root)
    progress('explaining', 0, 2, '生成并另次复核完整分项分析与代表案例；原评分保留')
    result = enrich(entry_ref, root, client)
    if result['status'] != 'basis_checked':
        raise Paused('needs_review', '完整说明要求重新核对原评分，原分及实质异议保留，未接入候选')
    # enrich已复验权威原判及全部说明回执；装配只消费该次冻结manifest的对象快照。
    accepted = read_ref(entry_ref)
    stage_ref = reference(root/'result.json'); stage = read_ref(stage_ref)
    stamp = read_ref(stage['accepted_input'])
    if stage['result'] != result or stamp['accepted_entry'] != entry_ref:
        raise ValueError('完整说明结果与本次已核入口不符')
    manifest = read_ref(stage['basis_manifest'])
    data = read_ref(manifest['input'])['data']; spec = read_ref(manifest['spec'])
    bundle = read_ref(accepted['files']['bundle.json'])
    presentation = build(bundle, data, spec)
    if verify(entry_ref, root, client.config) != result:
        raise ValueError('装配期间完整说明的原判或复查链变化，保留原件待核')
    # 只读复验核的是当前文件；装配所用快照也必须仍属这条链，防止先替换后恢复的ABA。
    read_ref(stage_ref)
    read_ref(stage['basis_manifest'])
    read_ref(manifest['input'])
    read_ref(manifest['spec'])
    read_ref(entry_ref)
    freeze(root/'presentation-input.json', dict(version=VERSION, stage=stage_ref,
        assembler=reference(__file__)))
    freeze(root/'presentation-bundle.json', presentation)
    progress('explaining', 2, 2, '完整分项分析及案例另次复核通过；等待接入候选与人工审核')
    return presentation
