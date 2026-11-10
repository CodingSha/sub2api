"""OPT010：收敛既有判据对象，并移除请求中的历史派生评语。"""
import copy

from . import candidate_review_context as previous, repair_scope
from .common import require

VERSION = 'pmje-observation-20261103.14.10-judgment1'
FEEDBACK_VERSION = previous.FEEDBACK_VERSION
REQUEST_VERSION = 'judgment-request-20261103.1'
PERFORMANCE = previous.PERFORMANCE
ITEMS = previous.ITEMS
RUBRIC = copy.deepcopy(previous.RUBRIC)
RUBRIC['version'] = VERSION
RUBRIC['performance_anchors']['P1']['verified'] = (
    '已将实际存在歧义的目标边界与有关材料或先前冲突作有据对照，'
    '明确哪些歧义已解决及剩余边界；核对对象是目标边界，不是软件实现验收')
RUBRIC['performance_anchors']['E2']['adapted'] = (
    '根据当时已知的失效线索及可用条件，选择相称的止损时点、替代方式及保留范围；'
    '不要求事先查明底层完整因果或已经解决阻塞')
RUBRIC['performance_anchors']['E3']['adapted'] = (
    '比对当前需求与既有成果的差异，选择适配或保持不变的范围及维护方式；'
    '正常调用和打开成功本身不代替这一选择')


def _replace_once(text, old, new):
    require(text.count(old) == 1, '既有说明替换目标不唯一')
    return text.replace(old, new)


# 替换原有支持范围说明，不在继承指令末尾继续追加规则。
INSTRUCTIONS = _replace_once(previous.INSTRUCTIONS,
    '在判无机会或未展示前，核对本项当期实质动作、关键反馈与本人后续选择、具体已证缺口及必要前后文；预览不足先补读。在现有fact_coverage和cells中说明影响有效范围、归责、主归或表现依据的候选如何使用、合并或排除，同义可合并，背景不另奖励。不能换选基础动作抹掉已有条件选择或核对，也不能用局部最高档代替整期轨迹。保留本人各动作的实际作用、具体缺口及未验边界。',
    '评价单元是本项的任务×期间整体轨迹。先综合本项当期有效要求、实质选择、关键反馈及已证缺口，再在既有action.fact中概括本人处理；不要先选一句代表动作，再用它推断整期未见其他表现。reason与progression须处理原文或context中已确认且足以改变本项结论的重要支持/反证，说明如何采用、合并或排除及支持范围；不逐句计分，不要求每句入账。相关事实若主归别项，按实际独立性说明，不以关联替代无机会，也不强造关联。预览不足先补读，不用局部最高档代替整期轨迹，保留真实缺口和未验边界。')
INSTRUCTIONS = _replace_once(INSTRUCTIONS,
    '核对对象以本项所评动作决定。P1核对的是实际目标边界及先前冲突；作有据对照并明确解决可符合verified，不能以软件未完成或未验收否定该目标核对，也不因此自动认定整期complete。E3某次实际复用已有适配核对时应保留该事实，不能换选另一尚未核查的复用对象便宣称完全未见核对；同样不能拿一个局部最高档代表所有复用或抹掉未处理必要缺口。每项先覆盖本人当期实际轨迹，再说明判据支持的范围；不因是周或月而更换尺子。',
    '各项对象及阶段以rubric.performance_anchors为准，按同项effective→adapted→verified递进。progression的fact须说明同一处理对象：effective确认实际做了什么，adapted说明根据什么当时条件作了何种实质选择，verified才说明对该选择已有何种相称核对。不能把同一个正常调用改称适配，再用调用成功补足前提；不能把尚无成功反馈作为针对性选择未发生的理由。已有局部核对须保留其范围，不能换对象后宣称整期未见，也不能以该局部代表整个任务。按实际任务范围综合判断，不另设最高、最低或平均档政策，不因周月名称换尺子。')


def prepare_payload(data):
    """只移除索引派生评语；原文、来源限制、期间及补读集合原样保留。"""
    task = data.get('task', {})
    if 'source_gaps' not in task:
        return data
    clean_task = dict(task)
    clean_task.pop('source_gaps')
    return dict(data, task=clean_task)


class JudgmentClient(repair_scope.ScopedClient):
    """在现有发送入口清理索引评语，不增加模型调用或评审环节。"""
    def call(self, instructions, data, cache, run_id):
        return super().call(instructions, prepare_payload(data), cache, run_id)


def check(unit, value):
    """原合同校验及诊断不变；不以关键词或引文数量判断语义。"""
    require(value.get('version') == VERSION, '候选版本错误')
    return previous.check(unit, dict(value, version=previous.VERSION))
