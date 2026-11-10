"""v10：按真实父任务及当期适用要求评分，阶段、复杂资格与缺证分开。"""
import copy
from fractions import Fraction

from .common import require, moment, display
from .policy import ITEMS, DIMENSIONS
from .revised_review import find_proof
from .staged_review import apply_monthly

VERSION = 'pmje-trial-20261101.10.1'
BANDS = {'critical_breach': 0, 'major_gap': 40, 'necessary_gap': 60,
         'partial': 70, 'sufficient': 80, 'tailored': 90, 'complete': 100}
STATES = {'scored', 'unknown', 'no_opportunity', 'related_only', 'not_applicable'}
RUBRIC = {
 'version': VERSION,
 'items': {k: {'name': v['name'], 'scope': v['scope']} for k, v in ITEMS.items()},
 'bands': {
  'critical_breach': '0：本人主动关键越界，知情可控并有实际影响。',
  'major_gap': '40：关键必要控制严重缺失或错误，尚未妥善处理。',
  'necessary_gap': '60：有有效动作，但有当时可知可控、存在可行替代的具体必要缺口；不要求事故或本人承认。',
  'partial': '70：行为已能支持推进，但对实际存在、非必要性的适配问题处理有限；必须给具体问题及上下文，不能仅因没有解释或没见后续而判70。',
  'sufficient': '80：本人行为充分满足当前阶段已知必要要求，有具体正面事实。适配/边界未能进一步确认不补为90/100，不抹掉80。',
  'tailored': '90：在80基础上，对本次实际条件作了有意义的选择、取舍、安排或调整，适配理由从原文与上下文可确认；不强求显式写因为或遇到特殊冲突。',
  'complete': '100：实际适用要求清单明确且全部有正面事实支持，对当前具体行为已充分覆盖；无需制造特殊冲突、多个阶段、后续反馈或未来成功。简单任务可100，但不能凭未发现错误默认100。',
 },
 'principles': [
  '先确认本人任务、时点、阶段及实际适用要求，再判分；要求清单从真实目标与上下文建立，不按抽象流程补齐所有可能要求。',
  'P评当时输入，M评本人安排与调整，J评核验安排/依据/采用，E评独立交互组织/止损/复用；安排充分可单独得高分，不冒充已执行或结果成功。',
  '高档与复杂条件独立。复杂资格需两实际条件交互、常规处理不足及本人决策对象；只影响M分层，不增加奖励项。',
  '每个真实父任务每子项每期间只判一条完整轨迹；同一目标跨组连续推进合并，独立成果分开。消息、提交、会话、阶段不等于任务数。',
  '同一父任务必要缺口未处理优先，不用正例均值抵消；及时纠正后按当期完整轨迹评并保留影响，不能用月末成功倒填之前周。',
  '普通请求不能为P/M/E重复奖励；同一句独立事实可分别评分，但须说明实质区别。AI自主工具或测试不归本人。',
  'unknown只用于实际需要判当前行为、且缺少足以改变当前档的决定性来源/主体/冲突。无特殊机会、未知更高档或未知未来成果不能整项弃评。',
  'no_opportunity须基于任务性质；实际已提出具体安排不能因执行未采集改成无机会。related_only须指向本任务当期已评分主项，不能循环或指向另一个未知。',
  '仅正常追问、失败结果、AI技术失误、平台重试、消息数、工具数、Token、费用均不证明本人低效。',
  '真实父任务内完整轨迹评分，任务等权→子项等权→维度等权；M有常规/复杂两层时70/30，只有一层不补另一层。缺维显示有据维度，不凑总分。',
  '周独立评价当期本人行为及期末前材料；月度整月重评，不平均周分。完整周未用/仅试探可有限扣减，最多15；采集未知不能当未用，首尾片段不扣。',
  '分数是版本化的产品标尺；等级与简单/复杂挑战范围、可评覆盖并列，不宣称潜在能力或绩效排名。',
 ]}

# 高档必须落实到本项有限的实际对象，不能另外要求用户制造复杂机会。
RUBRIC['item_anchors'] = {
 'P1': '100核目标对象、动作、当前成果/阶段边界是否已全部明确且相容；简单“解释X是什么”已明确对象与解释动作，可100。无需额外目标取舍或用户自己写完整清单。指代/范围仅部分可定位但能推进为80；有实际适配且范围仍有限为90。',
 'P2': '100核当前动作必要信息是否已可定位、当前已知冲突/未知是否相称说明；允许历史和附件。提供准确故障现象、页面、实际与预期值可充分，不要求额外材料组织设计。仅材料入口可读但分析所需内容完整性无法确认可80，不能把“入口存在”当“内容全面”。',
 'P3': '100核当时真实适用的要求是否相容、具体可执行、可检查；单一实际格式/权限要求充分可100，不要求额外例外。普通“继续/试着修复”主要归P1，不制造P3机会。',
 'M1': '100核实际独立方法、步骤或人机职责是否适合本任务，必要授权/分工已清楚；简单有效的一次分工可100，不必再遇失效替代。普通目标委派无独立方法归P1，不另奖M1。',
 'M2': '100核真实前置条件、交接或阻塞是否有相称的顺序、责任与接续；只评价当前安排可充分，不强求后续执行成功。一般先问能否再授权不自动构成独立依赖控制，须有实际前置或阻塞对象。',
 'M3': '100核本人是否按已知新事实将所有实际受影响的安排更新清楚，保留/待办去向相称；明确改计划即证明已改安排，不要求产物成功。反馈采用判断自身主归J3，不重复奖M3。',
 'J1': '100核对具体AI结论所需的依据、重要反证及当前不确定性已相称处理；单一清楚反例即可充分，不要求再造竞争解释或复查轮次。只有初步待核质疑可80，不能称已完成结论审查。',
 'J2': '100核当前对象所需的检查范围、方法/执行者是否相称且可执行；具体安排可充分，不要求等运行反馈。存在已知失效/覆盖限制须处理；泛泛测一下而无具体检查事实不自动补分。',
 'J3': '100核当前实际可见反馈是否获得有据接受、拒绝、纠正或保留未知的处置，采用范围与已验范围一致；一次具体反馈与正确处置可100，不要求未来业务成功或另一次复查。',
 'E1': '100核实际独立交互组织是否保留必要上下文、合理合并/增量接续并避免已知无效往返；不强求量化节省或跨多个会话。普通发消息或目标表达不构成额外E1表现。',
 'E2': '100核当前已知失效路径是否被相称停止/切换，实际需要保留或接续的部分已安排；停止决定可充分，不要求再遇遗留风险或最终执行反馈。没有失效不能制造机会。',
 'E3': '100核真实重复需要与已有资产之间的适配及当前已知差异是否得到相称安排；明确调用已有Skill可以充分，不要求其后运行成功或另有版本失效。只打算建Skill、只问有没有功能不算已安排复用。',
}
RUBRIC['calibration_examples'] = [
 {'input': '解释一下什么是API。', 'assessment': 'P1可100：对象、解释动作与当前概念咨询边界已明确；无额外目标冲突不降档。不能仅凭此句再奖M1或E1。'},
 {'input': '页面A中选择值X，实际请求却是Y；请定位原因。', 'assessment': 'P2若页面、现象和环境已由上下文定位，当前定位所需信息充分，可100；不要求用户预知根因或额外展示材料组织技巧。'},
 {'input': '先由你检查已有用例覆盖这次变更的哪些边界，再补缺失用例；我负责部署后的实际页面确认。', 'assessment': '独立核验安排和分工可分别在J2/M1按不同事实评价；当前安排可充分，但不宣称检查已执行或页面已通过。'},
 {'input': '帮我分析一下这个附件。', 'assessment': '初步分析目标可明确；P2若仅有入口而内容是否足够决定分析判断仍未确认，不补全内容质量；不得强制复杂输出规范。'},
]


def validate_period(data, period, submitted):
 """拒绝主体现身错误、未来引用、跨项复制、空泛高档与无据低档。"""
 out = copy.deepcopy(submitted)
 require(out.get('period_id') == period['period_id'], '期间错误')
 cells = out.get('cells', [])
 require(len(cells) == len(ITEMS) and {c.get('item') for c in cells} == set(ITEMS), '须处置全部12项')
 events = {e['event']: e for e in data['events']}
 scored = []
 for c in cells:
  require(c.get('status') in STATES and c.get('reason'), '状态或理由缺失')
  c['evidence'] = [find_proof(r, events, period) for r in c.get('evidence', [])]
  if c['status'] == 'scored':
   require(c.get('verdict') in BANDS and c.get('fact'), '缺档位或本人事实')
   require(c.get('assessment_stage') in {'input', 'decision', 'follow_through', 'closure'}, '缺评价阶段')
   c['person_evidence'] = [find_proof(r, events, period, personal=True) for r in c.get('person_evidence', [])]
   require(c['person_evidence'], '评分须有本期本人原文')
   require(all(r['event'] in data['person_events'] for r in c['person_evidence']), '本人依据跨任务')
   require(c.get('requirements') and c.get('adjacent_reason'), '缺适用要求及相邻档说明')
   require(all(isinstance(r, dict) and r.get('requirement') and r.get('assessment') for r in c['requirements']), '要求清单须逐项说明达到或缺口')
   c['band'] = BANDS[c['verdict']]
   if c['band'] >= 90: require(c.get('adaptation_basis'), '90以上缺具体适配或充分性依据')
   if c['band'] == 100:
    require(all(r.get('status') == 'met' for r in c['requirements']), '100须当前实际适用要求全部满足')
    for r in c['requirements']:
     require(r.get('evidence'), '100每条适用要求须正面依据')
     r['evidence'] = [find_proof(p, events, period) for p in r['evidence']]
   if c['band'] <= 70:
    require(all(c.get('responsibility', {}).get(k) for k in ['known_at_time', 'person_control', 'feasible_alternative', 'actual_gap']), '70及以下须具体缺口、当时可知可控与替代')
    require(c['evidence'], '低档须上下文反例依据')
   if c['item'] == 'M1': require(c.get('independent_method'), 'M1须超出普通请求的独立方法/分工')
   scored.append(c)
  else:
   require(c.get('band') is None and not c.get('verdict'), '非评分项不得补分')
   c['band'] = None
   if c['status'] == 'unknown':
    m = c.get('missing', {})
    require(m.get('type') in {'missing_source', 'attribution_conflict', 'evidence_conflict'} and m.get('needed') and m.get('why_changes_judgment'), '未知缺决定性缺口；流程未完成不得变人员未知')
   if c['status'] == 'related_only': require(c.get('primary_item') in ITEMS and c['primary_item'] != c['item'], '关联须指向本任务另一子项')
  if c['item'].startswith('M'):
   cx = c.get('opportunity_complexity', {})
   require(cx.get('status') in {'confirmed', 'none', 'pending'} and cx.get('reason'), 'M缺独立复杂资格')
   if cx['status'] == 'confirmed':
    require(c['status'] in {'scored', 'unknown'}, '无机会不能计为复杂处理机会')
    require(all(cx.get(k) for k in ['condition_a', 'condition_b', 'interaction', 'routine_insufficient', 'evidence']), '复杂缺两条件交互及常规不足')
    cx['evidence'] = [find_proof(r, events, period) for r in cx['evidence']]
 for i, a in enumerate(scored):
  for b in scored[i+1:]:
   require(a['fact'].strip() != b['fact'].strip(), '同一事实不能跨项复制')
   ra = {(p['event_id'], p['start'], p['end']) for p in a['person_evidence']}
   rb = {(p['event_id'], p['start'], p['end']) for p in b['person_evidence']}
   if ra & rb: require(a.get('independent_fact_difference') and b.get('independent_fact_difference'), '同引文跨项双方须说明实质区别')
 by = {c['item']: c for c in cells}
 for c in cells:
  if c['status'] == 'related_only': require(by[c['primary_item']]['status'] == 'scored', '关联必须终止于已评主项')
 return dict(out, user_id=data['user_id'], goal_id=data['goal_id'], title=data['title'], policy_version=VERSION)


def _mean(values):
 used = [Fraction(v) for v in values if v is not None]
 return sum(used, Fraction()) / len(used) if used else None


def summarize_period(rows):
 require(all(r.get('policy_version') == VERSION for r in rows), 'v10禁止混版本')
 require(len({(r['user_id'], r['period_id']) for r in rows}) <= 1, '单次汇总须同人同期间')
 require(len({r['goal_id'] for r in rows}) == len(rows), '同一真实任务不能重复权重')
 items = {}
 for item in ITEMS:
  cells = [next(c for c in r['cells'] if c['item'] == item) for r in rows]
  def pool(selected):
   scored = [c for c in selected if c['status'] == 'scored']; unknown = [c for c in selected if c['status'] == 'unknown']
   val = _mean(c['band'] for c in scored); n = len(scored)+len(unknown)
   num = sum((Fraction(c['band']) for c in scored), Fraction())
   return dict(exact=str(val) if val is not None else None, score=display(val), scored=len(scored), unknown=len(unknown),
    coverage_exact=str(Fraction(len(scored), n)) if n else None,
    source_sensitivity_low=str(num/n) if n else None,
    source_sensitivity_high=str((num+100*len(unknown))/n) if n else None)
  a = pool(cells); items[item] = a
  if item.startswith('M'):
   a['regular'] = pool([c for c in cells if c['opportunity_complexity']['status'] != 'confirmed'])
   a['complex'] = pool([c for c in cells if c['opportunity_complexity']['status'] == 'confirmed'])
 dims = {}
 for name, spec in DIMENSIONS.items():
  value = _mean(items[i]['exact'] for i in spec['items']); detail = {}
  if name == 'M':
   a, b = (_mean(items[i][layer]['exact'] for i in spec['items']) for layer in ('regular', 'complex'))
   value = a*Fraction(7,10)+b*Fraction(3,10) if a is not None and b is not None else a if a is not None else b
   detail = dict(regular_exact=str(a) if a is not None else None, complex_exact=str(b) if b is not None else None,
                 scope='regular_and_complex' if a is not None and b is not None else 'regular_only' if a is not None else 'complex_only' if b is not None else 'unobserved')
  detail.update(exact=str(value) if value is not None else None, score=display(value),
                covered_items=sum(items[i]['exact'] is not None for i in spec['items']),
                scored=sum(items[i]['scored'] for i in spec['items']), unknown=sum(items[i]['unknown'] for i in spec['items']))
  dims[name] = detail
 total = _mean(d['exact'] for d in dims.values()) if all(d['exact'] is not None for d in dims.values()) else None
 return dict(policy_version=VERSION, items=items, dimensions=dims, exact=str(total) if total is not None else None, score=display(total),
             scope='真实父任务等权；有据部分表现，缺证不估分；复杂资格与表现分列。')
