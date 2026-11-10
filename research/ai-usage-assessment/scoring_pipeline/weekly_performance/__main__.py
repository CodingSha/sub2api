"""读取既有整周输入与直接评审，离线校验、汇总并排他写入新结果。"""
import argparse
import copy
import hashlib
import json
from fractions import Fraction
from pathlib import Path

from ..common import require
from .review022 import VERSION, PERFORMANCE, check
from .scores022 import packed, summarize
from .review_policy import REVIEW_POLICY


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
    levels = sorted(PERFORMANCE, key=PERFORMANCE.get)
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
        if verdict not in levels:
            continue
        current = levels.index(verdict)
        for adjacent in (current-1, current+1):
            if not 0 <= adjacent < len(levels):
                continue
            changed = copy.deepcopy(rows)
            changed[0]['cells'][index]['performance']['verdict'] = levels[adjacent]
            next_verdict = levels[adjacent]
            changed[0]['cells'][index]['quality']['label'] = ('sufficient' if next_verdict in {'reasonable','good','outstanding','exceptional'} else 'minor_gap' if next_verdict=='partial' else next_verdict)
            hypothetical = summarize(changed, expected_keys=expected)
            delta = (Fraction(hypothetical['exact'])-Fraction(summary['exact'])
                     if summary['exact'] is not None else None)
            sensitivity.append(dict(item=cell['item'], from_verdict=verdict,
                hypothetical_verdict=levels[adjacent], total_change=packed(delta),
                hypothetical_total=packed(Fraction(hypothetical['exact'])
                                          if hypothetical['exact'] is not None else None)))
    monthly=unit.get('evaluation_scope')=='person_month'
    return dict(items=items, source_keys=sorted(keys), source_task_count=None if monthly else len(keys),
        source_week_count=len(keys) if monthly else None,source_kind='week' if monthly else 'task',
        numeric_item_count=summary['dimensions']['E']['item_count'],
        dimension_weight=summary['weights']['E'], sensitivity=sensitivity,
        scope='只代表当前已评分E内容；月度展示周来源关联数，不伪称任务数；来源关联数不是独立能力样本数，不称稳定效率。',
        sensitivity_scope='固定其他项，仅假设本项改至相邻表现档及相应质量分类，由同一公式处理必要缺口优先；不代表满足改判语义条件，无总分不补数值。')


def main():
    parser = argparse.ArgumentParser(description=VERSION+' 离线合同检查及精确周汇总（模型API请求0）')
    parser.add_argument('--input', required=True, help='已装配的person_week输入JSON；月度改用monthly_cli')
    parser.add_argument('--review', required=True, help='直接Codex评审正文JSON，非旧任务分或结果外壳')
    parser.add_argument('--expected-sources', required=True, help='独立冻结周索引的source_keys字符串数组JSON')
    parser.add_argument('--output', required=True, help='不存在的新JSON输出路径')
    parser.add_argument('--rules-version', choices=('1.5', '1.6'), default='1.6', help='旧判断复算必须显式选1.5，不自动升级语义版本')
    args = parser.parse_args()
    path = Path(args.output)
    try:
        require(not path.exists(), '输出已存在，拒绝覆盖')
        unit, input_ref = load(args.input)
        review, review_ref = load(args.review)
        expected, expected_ref = load(args.expected_sources)
        require(isinstance(expected, list) and expected
                and all(isinstance(k, str) and k for k in expected), '冻结来源须为非空字符串数组')
        require(unit.get('evaluation_scope') == 'person_week',
                '本入口只接收整周；月度须用scoring_pipeline.weekly_performance.monthly_cli汇总已核期间，禁止自由重判月档')
        if args.rules_version == '1.6':
            require(review.get('review_policy') == REVIEW_POLICY, '缺少当前语义合同声明，旧评审不能仅改规则版本升级')
        rows = check(unit, review)
        summary = summarize(rows, expected_keys=expected)
        result = dict(version=VERSION, rules_version=args.rules_version, review_policy=review.get('review_policy'), user_id=unit['user_id'], period_id=rows[0]['period_id'],
            summary=summary, efficiency=efficiency(unit, review, rows, summary, expected),
            inputs=dict(input=input_ref, review=review_ref, expected_sources=expected_ref),
            scope='当前已识别已评分范围的周使用表现；成果单列，周等级仅设计演示。',
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
