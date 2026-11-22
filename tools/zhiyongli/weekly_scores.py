"""五日抽样周分使用固定四维权重；未观察维度不贡献分值，原维度保留空值。"""
from fractions import Fraction
from runtime import RUBRIC

VERSION = 'weekly-fixed-coverage-20261122.3'
REASON = '周评分按提示词质量20%、任务驾驭度35%、判断核验30%、使用效率15%的固定权重合成；五日抽样未观察到的维度本周不贡献分值，维度仍显示未观察到。'


def total(dimensions):
    return sum((Fraction(d['exact']) * Fraction(RUBRIC['dimension_weights'][d['code']])
        for d in dimensions if d['exact'] is not None), Fraction())


def apply(result):
    from scoring_pipeline.weekly_performance.scores022 import packed
    result.update(packed(total(result['dimensions'])), score_reason=REASON)
    if result['score_status'] == 'no_activity':
        result['score_reason']='五个工作日的采集窗口内未观察到有效的本人AI使用记录，本周使用评分为0分。'
    else:
        result['score_status']='scored'
    return result
