"""离线对照同一冻结材料上的两次日评，可选人审基准，不发模型请求或导出评分。"""

import argparse
from fractions import Fraction
import json
from pathlib import Path

from request_retry import ATTEMPT, read_receipt
from runtime import RUBRIC, atomic, digest


def load(path):
    return json.loads(path.read_text())


def evidence_location(evidence, people):
    ref, quote, origin = (evidence[key] for key in ('source_ref', 'quote', 'origin'))
    source = people.get(ref, '')
    if not quote.strip() or source.count(quote) != 1 or origin not in {'direct_expression', 'quoted_or_forwarded'}:
        raise ValueError('引文缺本人原文唯一定位或来源类型')
    return ref, quote, origin


def validate_cells(cells, people):
    if cells.keys() != RUBRIC['items'].keys():
        raise ValueError('必须覆盖十二个分项，不可观察项须显式留空')
    spans = []
    for code, cell in cells.items():
        band, evidence = cell['band'], cell['evidence']
        if band is not None and (type(band) is not int or band not in RUBRIC['performance_values'].values()):
            raise ValueError('档位不符合日评规则：'+code)
        if (band is not None) != bool(evidence):
            raise ValueError('可观察性与证据不一致：'+code)
        for entry in evidence:
            ref, quote, origin = evidence_location(entry, people)
            if origin == 'quoted_or_forwarded' and not code.startswith('P'):
                raise ValueError('转贴不能为方法、判断或效率计分：'+code)
            start = people[ref].index(quote)
            end = start+len(quote)
            if any(ref==previous and code!=owner and start<right and left<end for previous, left, right, owner in spans):
                raise ValueError('同一段本人证据跨项重复：'+code)
            spans.append((ref, start, end, code))


def inspect_run(root, contract, packet):
    report = load(root/'report.json')
    if (report['period_type']!='day' or report['period_start']!=contract['day']
            or str(report['user_id'])!=str(packet['user_id']) or report['user_email']!=packet['user_email']
            or report['pilot']['quality_status']!='model_checked' or load(root/'quality.json')['quality_status']!='model_checked'):
        raise ValueError('没有完成与冻结身份及日期相符的本地日评')
    cells = {}
    for item in report['items']:
        code, exact = item['item_code'], item['exact']
        number = Fraction(exact) if exact is not None else None
        if code in cells or (number is not None and (number.denominator!=1 or item['state']!='observed')) or (number is None and item['state']!='unobservable'):
            raise ValueError('报告存在重复项、非法档位或未决状态：'+code)
        cells[code] = dict(band=int(number) if number is not None else None, evidence=item['evidence'])
    if any(root.glob('*.reused.json')):
        raise ValueError('盲测不得复用上一轮判档回执')
    paths = [path for path in root.glob('*.receipt.json') if not ATTEMPT.match(path.name)]
    if not paths:
        raise ValueError('缺少独立模型运行回执')
    response_ids, tokens = set(), []
    for path in paths:
        receipt = read_receipt(path)
        response = receipt.get('response', {})
        identity = response.get('id')
        if receipt.get('status')!='received' or response.get('status')!='completed' or not identity or identity in response_ids:
            raise ValueError('模型回执未终结、缺身份或被重复使用')
        response_ids.add(identity)
        value = (response.get('usage') or {}).get('total_tokens')
        if type(value) is int and value>=0:
            tokens.append(value)
    metrics = load(root/'metrics.json')
    return cells, response_ids, dict(request_attempts=metrics['requests'], completed_requests=len(paths), reported_requests=len(tokens),
        reported_tokens=sum(tokens) if tokens else None, wall_seconds=metrics['elapsed_seconds'],
        model_seconds=metrics['model_seconds'], max_input_bytes=max(metrics['input_bytes'].values(), default=0))


def compare(reference, packet, run_dirs):
    if reference is not None and (not reference['reviewer'].strip() or reference['method']!='independent_human_blind'):
        raise ValueError('缺少独立人审标注者与方式声明')
    if len(run_dirs)!=2 or run_dirs[0].resolve()==run_dirs[1].resolve():
        raise ValueError('须提供两个独立运行目录')
    packet_hash = digest(packet)
    contracts = [load(root/'contract.json') for root in run_dirs]
    contract = contracts[0]
    if (contract!=contracts[1] or contract['packet_sha256']!=packet_hash
            or contract['model']!='deepseek-v4.1-flash-local' or contract['policy_sha256']!=digest(RUBRIC)):
        raise ValueError('两轮运行、冻结原件或评分合同不一致')
    selections = [load(root/'selected-tasks.json') for root in run_dirs]
    selection_hash = digest(selections[0])
    if not selections[0] or digest(selections[1])!=selection_hash:
        raise ValueError('两轮须使用同一份冻结任务材料')
    if reference is not None and (reference['packet_sha256']!=packet_hash or reference['day']!=contract['day']
            or reference['selected_tasks_sha256']!=selection_hash):
        raise ValueError('人审基准与冻结原件、任务选材或日期不一致')
    people = {row['id']: row['text'] for task in selections[0] for row in task['records'] if row['role']=='user'}
    if reference is not None:
        validate_cells(reference['items'], people)
    reports, scored, used_ids = [], [], set()
    for root in run_dirs:
        cells, response_ids, metrics = inspect_run(root, contract, packet)
        if used_ids.intersection(response_ids):
            raise ValueError('两个运行目录使用了相同的模型响应')
        used_ids.update(response_ids)
        validate_cells(cells, people)
        result = dict(measurements=metrics)
        if reference is not None:
            result.update(extra_scored=[], missed=[], band_disagreements=[], evidence_review=[])
        for code, expected in (reference['items'].items() if reference is not None else ()):
            actual = cells[code]
            if expected['band'] is None and actual['band'] is not None:
                result['extra_scored'].append(code)
            elif expected['band'] is not None and actual['band'] is None:
                result['missed'].append(code)
            elif actual['band']!=expected['band']:
                result['band_disagreements'].append(dict(item=code, human=expected['band'], model=actual['band']))
            if actual['band'] is not None and expected['band'] is not None and any(not any(
                    gold['source_ref']==entry['source_ref'] and gold['origin']==entry['origin'] and gold['quote'] in entry['quote']
                    for gold in expected['evidence']) for entry in actual['evidence']):
                result['evidence_review'].append(code)
        reports.append(result)
        scored.append(cells)
    first, second = scored
    changes = [dict(item=code, first=first[code]['band'], second=second[code]['band'])
               for code in first if first[code]['band']!=second[code]['band']]
    result = dict(mode='independent_human_blind' if reference is not None else 'repeat_only',
        reference_sha256=digest(reference) if reference is not None else None,
        contract_sha256=digest(contract), selected_tasks_sha256=selection_hash, runs=reports,
        repeat_band_changes=[change['item'] for change in changes], repeat_band_details=changes,
        repeat_observation_changes=[change['item'] for change in changes if None in (change['first'], change['second'])],
        repeat_evidence_changes=[code for code in first if first[code]['band'] is not None and second[code]['band'] is not None
            and {evidence_location(entry, people) for entry in first[code]['evidence']}
            != {evidence_location(entry, people) for entry in second[code]['evidence']}],
        limitation='仅比较固定选材上的两轮判档，不覆盖全日选材或推算货币成本，不认定上线通过。')
    result['limitation'] += ('人审来源需人工核实，替代引文不直接认定为错误。' if reference is not None
                             else '无独立人审真值，不能据此估计评分准确率或把波动认定为错判。')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--reference', type=Path, help='独立人审盲标文件')
    mode.add_argument('--repeat-only', action='store_true', help='无人工真值，仅核对重复运行')
    for name in ('packet', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--run', type=Path, nargs=2, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('输出已存在，避免覆盖验收原件')
    try:
        reference = load(args.reference) if args.reference else None
        if args.reference and not isinstance(reference, dict):
            raise ValueError('人审基准必须是 JSON 对象')
        result = compare(reference, load(args.packet), args.run)
    except (ValueError, KeyError, TypeError, OSError, ZeroDivisionError):
        parser.exit(2, '盲测输入或回执验证失败，未写入结果；在私有目录内核对原件与运行合同。\n')
    atomic(args.output, result)


if __name__=='__main__':
    main()
