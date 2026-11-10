"""沿用v13聚合政策，仅将已校验的AI表现标签映射为既有分值。"""
from collections import Counter
from fractions import Fraction

from .common import require, display
from .stable_review import VERSION, PERFORMANCE


def mean(values):
    values=list(values)
    return sum(values,Fraction())/len(values) if values else None


def packed(value):
    return dict(exact=str(value) if value is not None else None,
                score=display(value) if value is not None else None)


def summarize(rows):
    """同人同期间：任务内必要缺口优先，否则等权；任务及四维仍等权。"""
    require(len({(r['user_id'],r['period_id']) for r in rows})<=1,'须同人同期间')
    require(len({r['key'] for r in rows})==len(rows),'任务重复')
    dimensions={}
    for dim in 'PMJE':
        tasks=[];states=Counter();bands=Counter();unresolved=0
        for row in rows:
            cells=[c for c in row['cells'] if c['item'].startswith(dim)]
            states.update(c['state'] for c in cells)
            known=[]
            for cell in cells:
                if cell['state']!='observed':continue
                verdict=cell['performance']['verdict']
                if verdict=='unresolved':unresolved+=1;continue
                require(verdict in PERFORMANCE,'未知表现标签')
                band=PERFORMANCE[verdict];known.append(band);bands[band]+=1
            if known:
                value=Fraction(min(known)) if min(known)<=60 else mean(map(Fraction,known))
                tasks.append(dict(key=row['key'],**packed(value),scored_items=len(known)))
        value=mean(Fraction(t['exact']) for t in tasks)
        dimensions[dim]=dict(**packed(value),task_count=len(tasks),task_components=tasks,
                             states=dict(states),bands=dict(bands),unresolved=unresolved)
    total=mean(Fraction(d['exact']) for d in dimensions.values()) if all(d['exact'] is not None for d in dimensions.values()) else None
    return dict(version=VERSION,**packed(total),dimensions=dimensions,
                scope='已观察表现的试评结果；无复杂倍数、参与扣分或缺失补分；不代表校准后的潜在能力或绩效排名。')
