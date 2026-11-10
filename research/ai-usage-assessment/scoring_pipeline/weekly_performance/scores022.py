"""023周/月各一次语义综合后精确汇总；固定PMJE权重，未知不补。"""
from collections import Counter
from fractions import Fraction
import json
from pathlib import Path
from ..common import require, display
from .review022 import VERSION, PERFORMANCE, ITEMS, RUBRIC

POLICY=json.loads((Path(__file__).resolve().parents[2]/'score-levels-g2.json').read_text())
WEIGHTS={d:Fraction(v) for d,v in RUBRIC['dimension_weights'].items()}
require(set(WEIGHTS)==set('PMJE') and sum(WEIGHTS.values(),Fraction())==1, '固定权重须恰为PMJE且和为1')


def level_for(value):
    """只用未舍入精确总分映射G2，不加任何行为资格。"""
    if value is None:return None
    require(not isinstance(value,(bool,float)), '评级须用精确数值，拒绝布尔和二进制浮点')
    value=Fraction(value)
    require(0 <= value <= 100, '评级分数越界')
    return next(b['level'] for b in POLICY['levels']
        if Fraction(b['min']) <= value and (value < Fraction(b['max']) or b['upper_inclusive'] and value == b['max']))

NECESSARY={'critical_breach','major_gap','necessary_gap'}

def mean(values):
    values=list(values)
    return sum(values,Fraction())/len(values) if values else None

def packed(value):
    return dict(exact=str(value) if value is not None else None,
                score=display(value) if value is not None else None)

def summarize(rows,*,expected_keys):
    """expected_keys来自冻结周索引，仅核覆盖，不作为数量加权或能力门槛。"""
    require(len(rows)==1,'须为一次已识别范围的周判定，不能拼接旧任务分或平均局部复验')
    row=rows[0]
    require(row.get('evaluation_scope') in {'person_week','person_month'},'局部验证不是周/月总分')
    monthly=row['evaluation_scope']=='person_month'
    scope='scored_month_scope' if monthly else 'scored_week_scope'
    require(expected_keys and len(expected_keys)==len(set(expected_keys)), '冻结来源集合缺失/重复')
    require(len(row.get('source_keys',[]))==len(set(row.get('source_keys',[]))),'周来源不能重复计入')
    require(set(row.get('source_keys',[]))==set(expected_keys),'周来源集合不完整或混入其他版本')
    require(len(row['cells'])==12 and {c['item'] for c in row['cells']}==set(ITEMS),'原12项须恰一次')
    dimensions={}
    for dim in 'PMJE':
        cells=[c for c in row['cells'] if c['item'].startswith(dim)]
        known=[];gaps=[];unresolved=[]
        for cell in cells:
            if cell['state']!='observed':continue
            verdict=cell['performance']['verdict']
            if verdict=='unresolved':unresolved.append(cell['item']);continue
            require(verdict in PERFORMANCE,'旧标签或未知标签不能重标迁移')
            require(cell['performance'].get('scope')==scope,'只能汇总对应周/月已评分范围的判断')
            score=Fraction(PERFORMANCE[verdict])
            known.append(dict(item=cell['item'],verdict=verdict,**packed(score)))
            if cell['quality']['label'] in NECESSARY:gaps.append(score)
        value=min(gaps) if gaps else mean(Fraction(c['exact']) for c in known)
        dimensions[dim]=dict(**packed(value),item_count=len(known),item_components=known,
            states=dict(Counter(c['state'] for c in cells)),unresolved=unresolved,
            rule='已证未处理必要缺口优先' if gaps else '当前可评独立子项等权',
            scope='仅代表当前已识别已评分的本维内容；不外推未展示子项或稳定潜在能力。')
    total=sum((WEIGHTS[d]*Fraction(dimensions[d]['exact']) for d in 'PMJE'),Fraction()) if all(d['exact'] is not None for d in dimensions.values()) else None
    level=level_for(total)
    rounded=display(total)
    crossed=total is not None and level_for(Fraction(rounded)) != level
    return dict(version=VERSION,**packed(total),dimensions=dimensions,
        weights={d:str(WEIGHTS[d]) for d in 'PMJE'},source_keys=list(row['source_keys']),
        evaluation_scope=row['evaluation_scope'],window=row.get('window'),source_gaps=row.get('source_gaps',[]),
        rating=dict(policy=POLICY['version'],level=level,status='待评价' if total is None else ('月度评级候选' if monthly else '周等级设计演示'),
            exact=str(total) if total is not None else None,rounding_crosses_boundary=crossed,
            score_display=(rounded+'（未舍入为'+str(total)+'，仍按'+level+'）') if crossed else rounded,
            note='只由未舍入总分映射；来源缺口单列，不代表无机会，不外推稳定能力。'),
        scope=('本月' if monthly else '本周')+'当前已识别已评分范围内AI使用表现；不外推稳定潜在能力。',
        E_interpretation='E按当前已观察内容评价，固定占15%，须同时展示来源、内容、分母和敏感性；不按覆盖数量改权，不称稳定效率。')
