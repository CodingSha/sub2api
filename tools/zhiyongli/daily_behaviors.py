"""独立行为与唯一评分归属的合同；字句重叠不等于重复行为，语义仍由评审核对。"""
import re
import math
from runtime import RUBRIC, digest


def source_fragments(delivered):
    """仅为入选任务的本人消息建立少量原文定位点；完整上下文不拆开评审。"""
    result={}
    for ref,row in delivered.items():
        if row['role']!='user':continue
        text=row['text']
        spans=[m.span() for m in re.finditer(r'[^，,。；;！？!?\n]+[，,。；;！？!?\n]*',text) if m.group().strip()]
        width=max(1,math.ceil(len(spans)/4))
        for offset in range(0,len(spans),width):
            start=spans[offset][0];end=spans[min(offset+width,len(spans))-1][1]
            identity='f'+digest([ref,start,end])[:16]
            result[identity]=dict(record_id=ref,quote=text[start:end])
    return result


def original_quote(text, quote):
    """仅恢复模型把NBSP等空白写成普通空格的引文，不容许改词或省略。"""
    if quote and text.count(quote)==1:return quote
    compact=[];positions=[]
    for match in re.finditer(r'\s+|\S',text):
        compact.append(' ' if match.group().isspace() else match.group())
        positions.append((match.start(),match.end()))
    needle=re.sub(r'\s+',' ',quote)
    normalized=''.join(compact)
    if not needle or normalized.count(needle)!=1:
        raise ValueError('行为引文须唯一定位到实际本人消息；不要改写或引用AI回复')
    start=normalized.index(needle);end=start+len(needle)
    return text[positions[start][0]:positions[end-1][1]]


def catalog(rows, delivered):
    """编号只由来源和具体主张生成，不因换评分项而成为新的行为。"""
    result={}
    spans=[];errors=[]
    for row in rows:
        claim=row['claim'].strip()
        identity='b'+digest([row['record_id'],claim])[:20]
        if identity in result:
            errors.append('同一独立行为被重复分配，不能换项计分')
        if 'quote' in row:
            source=delivered.get(row['record_id'],{})
            text=source.get('text','')
            try:quote=original_quote(text,row['quote'])
            except ValueError:
                errors.append('行为引文须唯一定位到实际本人消息：'+row['primary_item']+'；不要改写或引用AI回复')
                continue
            start=text.index(quote);end=start+len(quote)
            for ref,lo,hi,code in spans:
                if ref==row['record_id'] and code!=row['primary_item'] and start<hi and lo<end:
                    errors.append('同段原话跨项重叠：'+code+'/'+row['primary_item']+'；仅保留最直接归属，独立主张须选各自原话片段')
            spans.append((row['record_id'],start,end,row['primary_item']))
        result[identity]=dict(primary_item=row['primary_item'],source_ref=row['record_id'],claim=claim)
    if errors:raise ValueError('\n'.join(dict.fromkeys(errors)))
    validate(result,delivered)
    return result


def validate(behaviors, delivered):
    if not behaviors or len(behaviors)>40:
        raise ValueError('日评缺少独立行为归属或行为数量超限')
    claims=set()
    for identity,behavior in behaviors.items():
        row=delivered.get(behavior['source_ref'])
        key=(behavior['source_ref'],behavior['claim'].strip())
        if (not identity or behavior['primary_item'] not in RUBRIC['items'] or not row or row['role']!='user'
                or not behavior['claim'].strip() or len(behavior['claim'].encode())>1200 or key in claims):
            raise ValueError('独立行为缺少本人原文、具体主张或唯一主归')
        claims.add(key)


def check_evidence(behaviors, code, identity, source_ref, quote):
    behavior=behaviors.get(identity)
    if (not behavior or behavior['primary_item']!=code or behavior['source_ref']!=source_ref or not quote):
        raise ValueError('计分依据须绑定该分项独立行为，不能跨项重复计分')
