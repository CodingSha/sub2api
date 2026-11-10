"""从已核且签名冻结的期间行离线汇总月分；不生成引文或语义档位。"""
import argparse
import hashlib
import json
from pathlib import Path

from ..common import require
from .month_rollup import VERSION, SCALE_VERSION, summarize_month


def verify_ref(ref):
    """引用必须含实际文件签名；文件漂移时拒绝续用旧核对状态。"""
    raw = Path(ref['path']).read_bytes()
    require(hashlib.sha256(raw).hexdigest() == ref['sha256'], '来源签名不一致：'+ref['path'])
    return raw


def load_ref(ref):
    return json.loads(verify_ref(ref))


def calculate(manifest):
    """语义已核是前序评审声明；本入口只核签名、覆盖及确定性计算。"""
    require(manifest.get('version') == VERSION and manifest.get('scale_version') == SCALE_VERSION,
            '月度/评分尺版本不一致')
    for ref in manifest.get('proofs', []):
        verify_ref(ref)
    audit = load_ref(manifest['source_audit'])
    # 原文只保留共享来源，逐份核签名，不复制正文。
    for ref in [audit['dataset'], audit['index'], *audit['source_refs']]:
        load_ref(ref)
    person = audit['users'][manifest['user_id']]
    rows = []
    for ref in manifest['periods']:
        snapshot = load_ref(ref)
        require(snapshot.get('validation') == 'native_023_bound_period_or_audited_empty',
                '须先完成原生期间合同核对；月入口不替代引文核对')
        for proof in snapshot['proofs']:
            verify_ref(proof)
        row = snapshot['row']
        require(row['user_id'] == manifest['user_id'], '期间归属与月份人员不一致')
        source = next((p for p in person['periods'].values()
                       if p['start'] == row['window']['start'] and p['end'] == row['window']['end']), None)
        require(source and source['exact_match'] is True and not source['missing'] and not source['extra'],
                '冻结来源时间覆盖未匹配')
        require(set(row['source_keys']) == set(source['source_keys']), '期间任务来源与独立覆盖核查不一致')
        if not row['source_keys']:
            require(source['expected_person_events'] == 0
                    and row['empty_source_check']['audit_sha256'] == manifest['source_audit']['sha256']
                    and row['empty_source_check']['audit_path'] == manifest['source_audit']['path'],
                    '空期间缺实际来源核查')
        rows.append(row)
    result = summarize_month(rows, start=manifest['start'], end=manifest['end'],
                             gap_dispositions=manifest.get('gap_dispositions', []))
    result['provenance'] = dict(periods=manifest['periods'], source_audit=manifest['source_audit'],
                                proofs=manifest.get('proofs', []))
    return result


def main():
    parser = argparse.ArgumentParser(description=VERSION+' 离线月度精确汇总（评分API请求0）')
    parser.add_argument('--input', required=True, help='已核期间行的签名清单JSON，不接收自由月度评审')
    parser.add_argument('--output', required=True, help='不存在的新结果JSON路径')
    args = parser.parse_args()
    try:
        path = Path(args.output)
        require(not path.exists(), '输出已存在，拒绝覆盖')
        raw = Path(args.input).read_bytes()
        result = calculate(json.loads(raw))
        result['manifest'] = dict(path=str(Path(args.input).resolve()), sha256=hashlib.sha256(raw).hexdigest())
        content = json.dumps(result, ensure_ascii=False, indent=2)+'\n'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as target:
            target.write(content)
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
        parser.exit(2, '月度汇总失败：'+str(exc)+'\n')
    print(json.dumps(dict(version=VERSION, output=str(path), publishable=result['publishable']), ensure_ascii=False))


if __name__ == '__main__':
    main()
