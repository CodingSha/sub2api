"""读取既有整周输入与直接评审，离线校验、汇总并排他写入新结果。"""
import argparse
import copy
import hashlib
import json
from fractions import Fraction
from pathlib import Path

from ..common import require
from .review021 import VERSION, check
from .scores021 import packed, summarize


def load(path):
    raw = Path(path).read_bytes()
    return json.loads(raw), dict(path=str(Path(path).resolve()),
                                 sha256=hashlib.sha256(raw).hexdigest())


def efficiency(unit, review, rows, summary, expected):
    """展示已有E动作与其来源关联；相邻档仅算术假设，不产生新评审。"""
    evidence = {e['id']: e for e in review['evidence']}
    sources = unit.get('source_units', [])
    require(len(sources) == len(expected) and {s['key'] for s in sources} == set(expected),
            '效率来源展示须保留与冻结集合一致的source_units')
    items, keys, sensitivity = [], set(), []
    positive = ['basic_control', 'selective_control', 'relational_control', 'verified_control']
    for index, cell in enumerate(rows[0]['cells']):
        if not cell['item'].startswith('E') or cell['state'] != 'observed':
            continue
        refs = cell['action']['refs']
        events = {evidence[r]['event'] for r in refs}
        linked = [s['key'] for s in sources if events.intersection(s.get('person_events', []))]
        require(linked, '效率本人动作未关联到来源任务')
        keys.update(linked)
        items.append(dict(item=cell['item'], fact=cell['action']['fact'], source_keys=linked,
                          evidence=[evidence[r] for r in refs]))
        verdict = cell['performance']['verdict']
        if verdict not in positive:
            continue
        current = positive.index(verdict)
        for adjacent in (current-1, current+1):
            if not 0 <= adjacent < len(positive):
                continue
            changed = copy.deepcopy(rows)
            changed[0]['cells'][index]['performance']['verdict'] = positive[adjacent]
            hypothetical = summarize(changed, expected_keys=expected)
            delta = (Fraction(hypothetical['exact'])-Fraction(summary['exact'])
                     if summary['exact'] is not None else None)
            sensitivity.append(dict(item=cell['item'], from_verdict=verdict,
                hypothetical_verdict=positive[adjacent], total_change=packed(delta),
                hypothetical_total=packed(Fraction(hypothetical['exact'])
                                          if hypothetical['exact'] is not None else None)))
    return dict(items=items, source_keys=sorted(keys), source_task_count=len(keys),
        numeric_item_count=summary['dimensions']['E']['item_count'],
        dimension_weight='1/4', sensitivity=sensitivity,
        scope='只代表当前已评分E内容；来源任务关联数不是独立能力样本数，不称稳定周效率。',
        sensitivity_scope='固定其他项、仅改变一个正向档的算术假设，不表示满足相邻档语义条件；无总分时不补敏感性数值。')


def main():
    parser = argparse.ArgumentParser(description=VERSION+' 离线合同检查及精确周汇总（模型API请求0）')
    parser.add_argument('--input', required=True, help='已装配的person_week原始输入JSON')
    parser.add_argument('--review', required=True, help='直接Codex评审正文JSON，非旧任务分或结果外壳')
    parser.add_argument('--expected-sources', required=True, help='独立冻结周索引的source_keys字符串数组JSON')
    parser.add_argument('--output', required=True, help='不存在的新JSON输出路径')
    args = parser.parse_args()
    path = Path(args.output)
    try:
        require(not path.exists(), '输出已存在，拒绝覆盖')
        unit, input_ref = load(args.input)
        review, review_ref = load(args.review)
        expected, expected_ref = load(args.expected_sources)
        require(isinstance(expected, list) and expected
                and all(isinstance(k, str) and k for k in expected), '冻结来源须为非空字符串数组')
        require(unit.get('evaluation_scope') == 'person_week', '仅接收person_week，拒绝旧任务评分及local')
        rows = check(unit, review)
        summary = summarize(rows, expected_keys=expected)
        result = dict(version=VERSION, user_id=unit['user_id'], period_id=rows[0]['period_id'],
            summary=summary, efficiency=efficiency(unit, review, rows, summary, expected),
            inputs=dict(input=input_ref, review=review_ref, expected_sources=expected_ref),
            scope='当前已识别已评分范围的周能力表现；成果单列，无L等级。',
            validation_scope='确定性合同与精确汇总，不证明语义正确，不构成独立评审或真人校准。')
        content = json.dumps(result, ensure_ascii=False, indent=2)+'\n'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as target:
            target.write(content)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, '离线汇总失败：'+str(exc)+'\n')
    print(json.dumps(dict(version=VERSION, output=str(path)), ensure_ascii=False))


if __name__ == '__main__':
    main()
