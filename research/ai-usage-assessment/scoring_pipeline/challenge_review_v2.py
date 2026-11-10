"""v13.2：接受模型显式给出的范围变化列表；不修改v13.1冻结文件。"""
import copy
import json
from . import challenge_review as first

VERSION = 'pmje-challenge-20261102.13.2'
EXECUTION_VERSION = 'challenge-batch-20261102.2'
batch, serial, base = first.batch, first.serial, first.base
QUALITY, DEPTHS, FACETS = first.QUALITY, first.DEPTHS, first.FACETS
_check = first.check_review


def check_review(unit, value):
    checked = copy.deepcopy(value)
    for ctx in checked.get('context_assessments', []):
        # 保留模型的显式空列表，规范为JSON字符串供旧合同的非空字段检查。
        if isinstance(ctx.get('scope_changes'), list):
            ctx['scope_changes'] = json.dumps(ctx['scope_changes'], ensure_ascii=False)
    return _check(unit, checked)


def activate():
    if batch.VERSION == VERSION:
        return
    first.activate()
    for module in (first, batch, serial, base, first.old_scoring):
        module.VERSION = VERSION
    first.check_review = check_review
    batch.check_review = serial.check_review = base.check_review = check_review
    batch.EXECUTION_VERSION = EXECUTION_VERSION
    rubric = copy.deepcopy(first.RUBRIC)
    rubric['version'] = VERSION
    batch.RUBRIC = rubric
    batch.INSTRUCTIONS = first.INSTRUCTIONS + '\nscope_changes可为说明字符串或显式列表，无变化可给空列表；其他背景说明须非空字符串。'


def prepare(source, target):
    activate()
    return first.prepare(source, target)


if __name__ == '__main__':
    activate()
    batch.main()
