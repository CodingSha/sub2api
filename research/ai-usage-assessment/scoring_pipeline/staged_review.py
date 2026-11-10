"""v9：按本人当前行为分层评价，缺证与后续结果分开。"""
import copy
from datetime import timedelta
from fractions import Fraction

from . import stratified_review as previous
from .calibrated_review import observations
from .common import require, moment, display
from .policy import ITEMS
from .revised_review import find_proof, level

VERSION = 'pmje-trial-20261101.9'
BANDS = {'critical_breach': 0, 'major_gap': 40, 'necessary_gap': 60,
         'adequate': 70, 'reasoned': 80, 'controlled': 90, 'complete': 100}
PARTICIPATION_MAX_DEDUCTION = Fraction(15)
# 每项四层都评价当时本人可控制的动作，不用AI最终成败代替。
ANCHORS = {
 'P1': ['对象、动作和当前目标可识别。', '按实际歧义明确阶段、范围或完成标准。', '对实际目标冲突明确优先序、保留与退出边界。', '通过本人后续核对或纠偏确认该冲突已消解，适用边界得到承接。'],
 'P2': ['所需背景足以支持当前动作，允许明确指向已有资料。', '按本次问题选择针对性的事实、版本或来源。', '面对真实来源差异或缺失，核对冲突并限定可用/待查范围。', '来源核对反馈与必要补充/修正有据，已知失效材料得到处置。'],
 'P3': ['当时适用的要求相容且可执行。', '依据具体使用场景提出可检查的要求或验收条件。', '对真实权限风险、要求冲突或例外明确规则、优先序和保留边界。', '本人依据承接/检查反馈修正或确认边界，未决例外已明确处置；不等于产物验收通过。'],
 'M1': ['有适合当前任务的独立方法、步骤、角色或权限选择。普通帮我分析不另评M1。', '依据具体条件作方法适配或分工选择，事实能解释为什么采用此法。', '实际路径受限或失效时，提出可接续替代，并处理具体职责、权限或资源边界。', '替代分工已获得实际承接或本人跟进，已知剩余边界有处置；不要求业务成功。'],
 'M2': ['识别并安排具体前置条件、依赖或阻塞。', '依据依赖关系安排先后或交接，方法适合当时条件。', '在实际依赖冲突或阻塞下，同时明确恢复条件、责任及保留/退出边界。', '具体阻塞已解除或完成有据交接，本人对剩余限制和后续推进作出相称处理。'],
 'M3': ['依据新事实更新执行安排。', '针对反馈调整范围、顺序或职责，并说明保留的有效工作。', '对相互影响的实际变更同步更新已完成部分、剩余责任和失效边界。', '调整已有承接/执行反馈，本人核对并处理未完成、失败或需退出部分。'],
 'J1': ['针对具体AI结论核对有关依据。', '根据具体反证、版本差异或竞争解释作出有据判断。', '核对关键反证与替代解释，明确当前证据局限和可成立范围。', '对照或复查反馈支持该判断，本人对仍未知或被推翻部分作明确处置。'],
 'J2': ['对当前具体对象安排相称的检查内容、方法或执行者；不要求后续结果已采集。泛泛测一下不自动有独立核验事实。', '针对具体风险选择检查范围、方法或执行者，选择依据有据。', '对实际检查限制或联合风险明确通过/失败/未知判据、责任及使用边界。', '检查已经执行或有据交接，本人对结果中的失败、未覆盖项和采用限制作出处理。'],
 'J3': ['有当前可见反馈支持接受、拒绝、纠正或保留未知的决定。', '根据具体差异、反证或影响范围作有据采用/纠正选择。', '对实际存在的通过、失败和未知部分分别明确采用条件与限制。', '通过复查反馈或有据交接确认必要纠正和限制得到承接，不把未验范围当通过。'],
 'E1': ['有具体、适度的交互组织，如增量接续、合并反馈、合理中断恢复；不因发过消息就默认良好。', '针对实际往返原因组织反馈或上下文，避免具体可避免的重复。', '在多处交接或上下文变化中保留必要信息与核验边界，组织可接续。', '实际反馈证明重复/断链得到处理，且必要信息与核验未被省略。'],
 'E2': ['面对已知无效过程，明确停止、切换或缩小。', '根据具体失效原因选择止损时点或替代措施。', '处理本次实际失败路径、保留范围、遗留改动及接续责任。', '停止/切换得到承接或执行反馈，本人对遗留和替代路径局限完成相称处置。'],
 'E3': ['真实重复需求下，明确指向已有成果、Skill、脚本或流程并安排复用，适配关系可确认；不要求先看到AI运行成功。仅打算建立资产、询问有没有接口不等于明确复用。', '依据新场景选择或调整复用方式，针对差异检查适用性。', '对实际版本/权限/输入变化、失效或维护负担明确复用边界与替代。', '存在实际复用承接/执行反馈，本人对变化或失效边界核对并作出处理；AI自主复用不归本人。'],
}
RUBRIC = {
 'version': VERSION, 'bands': BANDS,
 'items': {k: dict(name=ITEMS[k]['name'], scope=ITEMS[k]['scope'],
                  **{str(n): t for n, t in zip((70, 80, 90, 100), a)}) for k, a in ANCHORS.items()},
 'negative': {'60': '有正面处理但有具体必要缺口，普通可避免缺口即可，不要求事故或本人承认。',
              '40': '关键必要控制严重缺失或错误且未妥善处理。',
              '0': '本人有据的主动关键越界、知情可控且有实际影响。'},
 'principles': [
  '先确认本人具体机会、有效要求和时点，再评当前表达/选择安排/跟进/闭合；四个正向层次为70/80/90/100，低档为60/40/0。分数为产品标尺而非统计估计。不得机械把旧档平移。',
  '选择或安排可独立充分，不因执行结果未采集整项弃评；不知道更高档不抹掉已证基础表现。不能把安排写成已经实施。',
  '缺点可依当时上下文合理推断，须确认信息、控制、可行替代和具体缺口，不要求明知故犯或真实事故。没有记录不能自动证明没有处理。',
  '同一目标同一子项按完整轨迹判。未处理必要缺口优先，不被该目标普通正例抵消；及时妥善纠正保留纠正后的表现和影响，不永久封顶。不同独立目标仍分别评分。',
  '无本人动作不等于扣分；真实无独立机会给性质依据，不能因理论上都能测试就虚构核验机会。真正关键来源/主体缺失保留unknown。',
  '同一实质事实只主归一项。同句独立事实必须双方写明差别。普通诊断请求通常归P1，AI自主工具调用不构成本人M/J/E。',
  '复杂资格沿用v8两条件交互及常规路径不足，正负同权；M常规70%复杂30%，不得因改正向档位重新挑选复杂机会。',
  '周只看本周本人行为和期末前证据；月度整月汇总，不平均周分。四维各25%；参与仅作月度扣减，最多15分。',
  'unknown须指出缺什么、为何足以改变当前档及已查来源；不能通过转关联/无机会制造恢复评分。评审或检索失败是流程待办。',
  '保留同一家族/局部边界以便比较；全部位置重新处置，不为预设等级分布改判，不按工具数、轮数、Token或费用评分。',
 ]}


def validate_period(data, period, submitted):
 """复用成熟的主体/逐字/时间校验，档位和新增理由按v9独立核验。"""
 temp = copy.deepcopy(submitted)
 originals = {}
 for outer in temp['cells']:
  for c in observations(outer):
   if c['status'] != 'scored': continue
   key = (outer['item'], c.get('unit_id', '_whole'))
   originals[key] = copy.deepcopy(c)
   require(c.get('verdict') in BANDS, 'v9档位标签错误')
   require(c.get('assessment_stage') in {'input', 'decision', 'follow_through', 'closure'}, '缺本人评价阶段')
   require(c.get('rating_basis', {}).get('met') and c['rating_basis'].get('next_boundary'), '缺当前档支持及相邻档边界')
   if BANDS[c['verdict']] >= 80: require(c.get('anchor_basis'), '80以上缺具体适配依据')
   if BANDS[c['verdict']] >= 90: require(c.get('boundary_handling'), '90以上缺真实关键边界处理')
   if c['verdict'] == 'complete': require(c.get('closure_evidence'), '100缺承接/跟进/核对证据')
   # 旧校验仅用于证据结构，不将旧标尺数值带回新结果。
   if c['verdict'] == 'controlled': c['verdict'] = 'reasoned'
 out = previous.validate_period(data, period, temp)
 events = {e['event']: e for e in data['events']}
 for outer in out['cells']:
  for c in observations(outer):
   if c['status'] != 'scored': continue
   original = originals[(outer['item'], c.get('unit_id', '_whole'))]
   c['verdict'] = original['verdict']; c['band'] = BANDS[c['verdict']]
   if c['verdict'] == 'complete': c['closure_evidence'] = [find_proof(p, events, period) for p in c['closure_evidence']]
 out['policy_version'] = VERSION
 return out


def summarize_period(rows):
 require(all(r.get('policy_version') == VERSION for r in rows), 'v9汇总拒绝混版本')
 out = previous.summarize_period([dict(r, policy_version=previous.VERSION) for r in rows])
 out['policy_version'] = VERSION
 return out


def apply_monthly(performance, period, windows):
 """完整周参与不足最多扣15分，明确不适用须有依据，未知不算未用。"""
 start, end = moment(period['start']), moment(period['end'])
 require(start < end, '月度期间起止错误')
 first = start.replace(hour=0, minute=0, second=0, microsecond=0)
 first += timedelta(days=(7-first.weekday()) % 7)
 if first < start: first += timedelta(days=7)
 expected = set()
 while first + timedelta(days=7) <= end:
  expected.add((first, first+timedelta(days=7))); first += timedelta(days=7)
 full, seen = [], set()
 for w in windows:
  a, b = moment(w['start']), moment(w['end'])
  require(a < b and (a,b) not in seen, '参与窗口重复或起止错误'); seen.add((a,b))
  if (a,b) in expected: full.append(w)
 excluded = [w for w in full if w.get('applicability') == 'not_applicable']
 require(all(w.get('applicability_basis') for w in excluded), '排除参与周须有明确适用性依据')
 used = [w for w in full if w not in excluded]
 credit = {'substantive': Fraction(1), 'limited': Fraction(1,2), 'none': Fraction()}
 known = bool(used) and {(moment(w['start']),moment(w['end'])) for w in full} == expected and all(w.get('coverage') == 'confirmed' and w.get('activity') in credit for w in used)
 rate = sum((credit[w['activity']] for w in used), Fraction()) / len(used) if known else None
 deduction = PARTICIPATION_MAX_DEDUCTION*(1-rate) if rate is not None else None
 score = max(Fraction(), Fraction(performance)-deduction) if performance is not None and deduction is not None else None
 return dict(participation_windows=windows, complete_week_count=len(full), applicable_week_count=len(used),
  participation_exact=str(100*rate) if rate is not None else None, participation_score=display(100*rate) if rate is not None else None,
  participation_deduction_exact=str(deduction) if deduction is not None else None, participation_deduction=display(deduction),
  monthly_exact=str(score) if score is not None else None, monthly_score=display(score), trial_rating=level(score) if score is not None else None,
  participation_scope='完整适用自然周；实质1/仅试探0.5/确认未用0；首尾片段不扣；最多扣15分，不加分；采集未知不当未用。')
