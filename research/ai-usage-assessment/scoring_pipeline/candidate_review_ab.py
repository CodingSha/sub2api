"""OPT-002最小候选：事实覆盖及机会主归；复用14.7全部原判据。"""
import copy

from . import stable_review as base
from .common import require
from .revised_review import find_proof

VERSION = 'pmje-observation-20261103.14.8-ab1'
PERFORMANCE = base.PERFORMANCE
ITEMS = base.ITEMS
RUBRIC = copy.deepcopy(base.RUBRIC)
RUBRIC.update(version=VERSION, fact_coverage='同次评审的简明事实处置索引，不另评分、不按条数定档。')
INSTRUCTIONS = base.INSTRUCTIONS + '''
本候选只澄清事实覆盖及机会主归，其他判据不变，version使用rubric.version。
在判无机会或未展示前，覆盖当期本项相关的实质动作、具体已证缺口及必要前后文；预览不足先补读。不能仅选另一处基础动作，就抹掉已经存在的条件选择或核对；也不能取一个局部最高档代替整期轨迹。说明各事实支持的具体范围及未验边界，保留真实不足。
每个period_reviews增加一次fact_coverage:[{fact,refs:[],primary_item:子项或null,reason}]，列有区分作用的关键本人事实、具体缺口和被排除的候选；重复表达合并，不为12项逐个制造条目。primary_item是该独立事实的主归，排除/待定则null并在reason简明说明。refs沿用同一evidence账本，不复制原文。这个索引是同次判断的证据说明，不是新增评分层、复核器或数量门槛。
先辨独立事实、主归和基础机会，再依effective/adapted/verified判断。M1明确且可行的方法或职责选择可有基础机会，不要求先证明方法比较、复杂耦合或核对才承认机会；那些是较高表现证据。仅先看再改的阶段要求不自动产生独立M1；实际前置条件下的接续事实主归M2，另有独立方法选择才另评M1。目标对象名称、标准命令或当前改动可定位，本身不能在P1之外再次当P2独立信息准备；有另一个材料选择/补充事实才分项。同句确有不同事实可分项；换引文、换措辞或只写difference不证明事实独立。其他项同理，不把已主归的动作重复奖罚。
'''


def check(unit, value):
    """仅增加索引引用与期间校验；表现与语义继续由原校验器约束。"""
    require(value.get('version') == VERSION, '候选版本错误')
    # 临时适配校验入口的版本；不改模型原输出或历史模块的全局状态。
    rows = base.check(unit, dict(value, version=base.VERSION))
    refs = {e['id']: e for e in value['evidence']}
    events = {e['event']: e for e in unit['events']}
    periods = {p['period_id']: p for p in unit['periods']}
    coverage_by_period = {}
    for part in value['period_reviews']:
        coverage = part.get('fact_coverage')
        require(isinstance(coverage, list), '缺简明事实处置索引')
        for fact in coverage:
            require(isinstance(fact, dict) and fact.get('fact') and fact.get('reason'), '事实索引缺事实/处置理由')
            require('primary_item' in fact and (fact['primary_item'] is None or fact['primary_item'] in ITEMS), '事实索引主归错误')
            ids = fact.get('refs')
            require(isinstance(ids, list) and ids and set(ids) <= set(refs), '事实索引缺有效引用')
            for pid in part['period_ids']:
                for rid in ids:
                    find_proof(refs[rid], events, periods[pid])
        for pid in part['period_ids']:
            coverage_by_period[pid] = coverage
    for row in rows:
        row['fact_coverage'] = copy.deepcopy(coverage_by_period[row['period_id']])
    return rows
