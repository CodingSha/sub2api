"""只为引文格式修复提出原文候选；相似匹配不构成事实支持或自动修复。"""
from runtime import digest

PROSE_PUNCTUATION=frozenset('，。；、！？')
NUMERIC_CONTINUATION=frozenset('0123456789.+-−－．eE%％')


def significant(text):
    """仅忽略空白和中文句读；负号、小数点、代码及URL标点不改写。"""
    return ''.join(c for c in text if not c.isspace() and c not in PROSE_PUNCTUATION)


def propose(unit, slot, previous):
    """所有同字候选均保留，由模型明确选择；不跨事件、拼接或清洗返回原文。"""
    from bounded_read import raw_size, REQUEST_BYTES
    source=next((e for e in unit['events'] if e['event']==slot['event']),None)
    quote=previous['items'][slot['item']]['source_quotes'][slot['index']]
    if source is None or quote.get('event')!=source['event'] or not isinstance(quote.get('quote'),str):return []
    needle=significant(quote['quote'])
    if not needle:return []
    positions=[i for i,c in enumerate(source['text']) if significant(c)]
    haystack=''.join(source['text'][i] for i in positions)
    result=[];offset=0;source_sha=digest(source);candidate_bytes=2
    while True:
        found=haystack.find(needle,offset)
        if found<0:break
        start=positions[found];end=positions[found+len(needle)-1]+1
        exact=source['text'][start:end]
        # 不能只截出负数/小数/百分数的一部分，再用匹配证明其等于另一数值。
        if ((exact[0].isdigit() and start and source['text'][start-1] in NUMERIC_CONTINUATION)
                or (exact[-1].isdigit() and end<len(source['text']) and source['text'][end] in NUMERIC_CONTINUATION)):
            offset=found+1;continue
        identity=dict(event=source['event'],source_sha256=source_sha,start=start,end=end,quote=exact)
        candidate=dict(candidate_id=digest(identity),**identity,
            surrounding_text=source['text'][max(0,start-128):min(len(source['text']),end+128)])
        candidate_bytes+=raw_size(candidate)+bool(result)
        if candidate_bytes>REQUEST_BYTES:
            raise ValueError('引文全部原文候选超出请求容量，保留待核，不截取部分候选或重新评分')
        result.append(candidate)
        offset=found+1
    return result


def resolve(unit, feedback, value):
    """仅展开模型选中的冻结原文；不接受模型返回新的引文或改动候选列表。"""
    if not isinstance(value,dict) or set(value)!={'candidate_id'}:
        raise ValueError('引文候选修复只接受candidate_id')
    expected=propose(unit,feedback['quote_slot'],feedback['previous_audit'])
    if expected!=feedback['quote_candidates']:raise ValueError('引文候选与冻结原文不符')
    chosen=next((c for c in expected if c['candidate_id']==value['candidate_id']),None)
    if chosen is None:raise ValueError('未选择有效原文候选；引文仍未核实，保留原判断与异议')
    return dict(event=chosen['event'],quote=chosen['quote'])
