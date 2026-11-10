"""OPT-005：关键反馈与实际选择关联、P1核对范围；保持原分值与语义边界。"""
import copy

from . import candidate_review_linkage as previous, contract_feedback
from .common import require

VERSION = 'pmje-observation-20261103.14.8-ab3'
FEEDBACK_VERSION = contract_feedback.VERSION
PERFORMANCE = previous.PERFORMANCE
ITEMS = previous.ITEMS
RUBRIC = copy.deepcopy(previous.RUBRIC)
RUBRIC['version'] = VERSION


def _replace_once(text, before, after):
    require(text.count(before) == 1, '候选替换目标不唯一')
    return text.replace(before, after)


INSTRUCTIONS = _replace_once(previous.INSTRUCTIONS,
    '在判无机会或未展示前，覆盖当期本项相关的实质动作、具体已证缺口及必要前后文；预览不足先补读。不能仅选另一处基础动作，就抹掉已经存在的条件选择或核对；也不能取一个局部最高档代替整期轨迹。说明各事实支持的具体范围及未验边界，保留真实不足。',
    '预览不足先补读。对影响有效范围、归责、主归或表现依据的关键反馈，在现有fact_coverage与cells说明它与本人后续实际选择的关系及如何使用、合并或排除；同义可合并，背景不另奖励。保留各动作已证作用、具体缺口及未验边界；不能换选基础动作抹掉条件选择或核对，也不能用局部最高档代替整期轨迹。')
INSTRUCTIONS = _replace_once(INSTRUCTIONS,
    'P1对实际目标歧义作有据对照并明确解决，可符合verified，不要求未来实现验收。',
    'P1核对的是实际目标边界及先前冲突；作有据对照并明确解决可符合verified，不能以软件未完成或未验收否定该目标核对，也不因此自动认定整期complete。')


def check_semantics(unit, value):
    require(value.get('version') == VERSION, '候选版本错误')
    return previous.check(unit, dict(value, version=previous.VERSION))


def check(unit, value):
    return contract_feedback.check_with_diagnostics(unit, value, check_semantics)
