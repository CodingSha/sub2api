"""替换现有周汇总：现有子项一次周判定，按固定映射计算；不再平均任务档位。"""
from collections import Counter
from fractions import Fraction
from ..common import require, display
from .review021 import VERSION, PERFORMANCE, ITEMS

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
    require(row.get('evaluation_scope')=='person_week','局部验证不是个人新周分')
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
            require(cell['performance'].get('scope')=='scored_week_scope','只能汇总声明了当前已评分范围的判断')
            score=Fraction(PERFORMANCE[verdict])
            known.append(dict(item=cell['item'],verdict=verdict,**packed(score)))
            if cell['quality']['label'] in NECESSARY:gaps.append(score)
        value=min(gaps) if gaps else mean(Fraction(c['exact']) for c in known)
        dimensions[dim]=dict(**packed(value),item_count=len(known),item_components=known,
            states=dict(Counter(c['state'] for c in cells)),unresolved=unresolved,
            rule='已证未处理必要缺口优先' if gaps else '当前可评独立子项等权',
            scope='仅代表当前已识别已评分的本维内容；不外推未展示子项或稳定潜在能力。')
    total=mean(Fraction(d['exact']) for d in dimensions.values()) if all(d['exact'] is not None for d in dimensions.values()) else None
    return dict(version=VERSION,**packed(total),dimensions=dimensions,
        weights={d:'1/4' for d in 'PMJE'},source_keys=list(row['source_keys']),
        scope='本周当前系统已识别已评分范围内的AI使用能力表现，不是全域能力认证或稳定潜在能力。',
        E_interpretation='一次E照常反映当前已观察内容，不能称稳定效率；四维等权下本维仍占25%，须与来源、内容、分母和敏感性同时展示。')
