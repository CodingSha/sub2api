"""OPT-007：恢复否定判断的实质候选覆盖，沿用既有评分与校验。"""
import copy

from . import candidate_review_feedback as previous
from .common import require

VERSION = 'pmje-observation-20261103.14.8-ab4'
FEEDBACK_VERSION = previous.FEEDBACK_VERSION
PERFORMANCE = previous.PERFORMANCE
ITEMS = previous.ITEMS
RUBRIC = copy.deepcopy(previous.RUBRIC)
RUBRIC['version'] = VERSION

INSTRUCTIONS = previous._replace_once(previous.INSTRUCTIONS,
    '预览不足先补读。对影响有效范围、归责、主归或表现依据的关键反馈，在现有fact_coverage与cells说明它与本人后续实际选择的关系及如何使用、合并或排除；同义可合并，背景不另奖励。保留各动作已证作用、具体缺口及未验边界；不能换选基础动作抹掉条件选择或核对，也不能用局部最高档代替整期轨迹。',
    '在判无机会或未展示前，核对本项当期实质动作、关键反馈与本人后续选择、具体已证缺口及必要前后文；预览不足先补读。在现有fact_coverage和cells中说明影响有效范围、归责、主归或表现依据的候选如何使用、合并或排除，同义可合并，背景不另奖励。不能换选基础动作抹掉已有条件选择或核对，也不能用局部最高档代替整期轨迹。保留本人各动作的实际作用、具体缺口及未验边界。')


def check(unit, value):
    require(value.get('version') == VERSION, '候选版本错误')
    return previous.check(unit, dict(value, version=previous.VERSION))
