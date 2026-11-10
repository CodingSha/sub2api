"""OPT-003：落实已有主归与支持范围；不决定整期多动作聚合政策。"""
import copy

from . import candidate_review_ab as previous
from .common import require

VERSION = 'pmje-observation-20261103.14.8-ab2'
PERFORMANCE = previous.PERFORMANCE
ITEMS = previous.ITEMS
RUBRIC = copy.deepcopy(previous.RUBRIC)
RUBRIC['version'] = VERSION
INSTRUCTIONS = previous.INSTRUCTIONS + '''
事实处置必须与实际cells闭环：primary_item指向本次真正承载该事实的项，不能指向no_opportunity、not_applicable或related；同一来源至少出现在该项的依据中。关联事实指向最终主归项，排除或待定仍用null。这只检查说明与结论的明确矛盾，不按引用数定档。
P2的基础判据是当前必要信息可定位并足以推进，不要求先有来源比较或核验。本人独立报告的当前状态/观察可成为信息补充，须判断其对当前请求是否有实际作用；不因未作判断、未验收或未作采用决定就排除P2。排除具体事实时，reason直接说明与实际候选项基础判据的关系；说明不属于另一项不能代替这一判断。目标名称或时点仍不在P1之外重复计分。
延续既有M2口径：实际前置条件下的接续事实主归M2，包括依据服务/数据可得性安排推进、保留和等待。反馈后出现不自动使同一接续事实变成独立M3；另有执行范围或顺序调整动作时才单独评M3，并定位独立动作。不能只换选另一个较弱动作便宣布整期未见针对性处理。
复杂节点说明和表现依据必须分别定位实际动作。performance.reason说明档位由哪些动作支持；若challenge_handling涉及的是另一个动作，明确其当前已证处理程度，不能把前者档位借给后者。没有证据不得宣布整体驾驭良好；也不因尚未完成全部核验而统一降档。本候选不新增同项多动作的最高、最低、平均或其他合成规则。
'''


def _refs(obj):
    """仅收集已声明的来源链接，不分析事实含义。"""
    if isinstance(obj, list):
        return [rid for value in obj for rid in _refs(value)]
    if not isinstance(obj, dict):
        return []
    return [rid for key, value in obj.items()
            for rid in (value if key == 'refs' else _refs(value))]


def linkage_issues(value):
    """无机会与主归直接冲突、主归缺来源连线；不裁定null或引用蕴含。"""
    evidence = {e['id']: e['event'] for e in value['evidence']}
    issues = []
    for part in value['period_reviews']:
        cells = {c['item']: c for c in part['cells']}
        for index, fact in enumerate(part['fact_coverage']):
            item = fact['primary_item']
            if item is None:
                continue
            cell = cells[item]
            location = '/'.join(part['period_ids']) + f'/fact_coverage[{index}]/{item}'
            if cell['state'] in {'no_opportunity', 'not_applicable', 'related'}:
                issues.append(location + '：事实主归与该项状态矛盾')
            elif not ({evidence[r] for r in fact['refs']} & {evidence[r] for r in _refs(cell)}):
                issues.append(location + '：事实主归未连到该项来源')
    return issues


def check(unit, value):
    require(value.get('version') == VERSION, '候选版本错误')
    rows = previous.check(unit, dict(value, version=previous.VERSION))
    issues = linkage_issues(value)
    require(not issues, '; '.join(issues))
    return rows
