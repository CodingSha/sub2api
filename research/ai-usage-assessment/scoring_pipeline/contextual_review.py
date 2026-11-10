"""v12任务背景评审：仅在新进程配置旧执行核，不修改v11冻结源码或结果。"""
import argparse
import copy
import json
import re
import sys
from pathlib import Path

from . import batch_review as batch
from . import dsh_serial as serial
from . import dsh_v11 as base
from . import progressive_review as scoring
from .common import require
from .revised_review import find_proof

VERSION = 'pmje-trial-20261102.12'
EXECUTION_VERSION = 'contextual-batch-20261102.1'
OLD_VERSION = scoring.VERSION
OLD_CHECK = batch.check_review
OLD_PACKET = batch.packet

GUIDANCE = '''
本轮为v12背景驱动评审，取代前文v11版本称呼；数值映射和聚合政策保持不变。
先理解任务再判断，不把原任务标题、purpose、boundary_reason当不可质疑的事实。必须结合本人原文、必要AI及工具正文恢复当时有效目标、必要范围和依赖。工具名、call_id只用于查正文和对应结果，不按调用量、轮次、字数、文件数判断复杂或能力。
结果增加context_assessments:[{period_ids:[...],effective_request:"当时有效目标",necessary_scope:"必要工作及依赖，不写调用量代替",execution_scope:"哪些为必要范围、哪些仅AI探索；没有需要时如实说明",scope_changes:"本期新增/缩减与单纯恢复背景分别说清；没有则说明",evidence:[{event,quote}],boundary:{status:"consistent|uncertain|needs_correction",reason:"边界及主体核对",evidence:[{event,quote}]},task_complexity:{status:"ordinary|complex|unknown",reason:"任务条件及证据，不等于本人表现",evidence:[{event,quote}]}}]，恰覆盖全部期间；相同判断可合并period_ids。无实质任务时可明确说明而不是编造目标；context只用期末之前证据。原始材料不足仍保留未知，可确认的行为照常评；needs_correction写清拟纠错依据，不偷偷跨任务借本人证据。
任务复杂性、M子项本人决策机会、本人表现是三个问题。任务本身复杂但AI自主完成，不自动给本人M/J/E高档或M复杂权重。任务范围大不必然需要复杂决策；工具少不证明简单。依赖/兼容/风险交互应解释为什么必要，而不是列两个条件就成立。
M复杂资格confirmed须排除合理常规处理即可满足的解释。先后步骤、批准后实施、下载后改配、父ID产生后绑定、普通DTO字段接续，均不因有顺序就复杂。不能拿立即动手或永不动手两个不合理极端证明常规方法不足。若合理分阶段后仍有跨条件取舍/联动影响，说明其真实证据和本人具体决策；必要背景没读应先needs_events，不靠想象风险确认。opportunity_complexity.routine_insufficient须写清合理常规路径及其仍不足之处；不足未证时none或pending，不牵连有据行为档位。
低档须对照用户真正要求和当时选项：未选一个选项不等于拒绝它；补充字段名/大小写不等于拒绝已有兼容方案；提出新增需求不等于违背旧文档。用户仍明确保留某成果要求而AI自行遗漏实现，不能全部归责用户。用户指定的中间处理链与最终验收要求有歧义，先核对是否已解释限制、本人是否知情仍接受，不能由缺未来反馈推必要缺口。保留确有当时可知、可控、可行替代且未处理的缺口，不因这些边界而一律豁免。
80/90/100仍按同一对象递进。普通更新不自动90；有针对性选择可90，即使复杂资格none。100的实际核查必须针对90的同一处理对象，一个具体反例可相称充分；不要求再多一个工具/另一接口成功。不把AI自述、后期输出或换对象的核验拼接为本人的100。
本次不提供旧分和个人预期等级；不得为了分差改变判据。context_assessments的文字由你根据材料填写，程序只校验来源、版本和期间，不预填结论。
'''


def check_review(unit, value):
    require(value.get('policy_version') == VERSION, 'v12评分版本不符')
    periods = {p['period_id']: p for p in unit['periods']}
    events = {e['event']: e for e in unit['events']}
    seen = []
    contexts = value.get('context_assessments')
    require(isinstance(contexts, list) and contexts, '缺模型任务背景判断context_assessments')
    for context in contexts:
        require(all(isinstance(context.get(k), str) and context[k].strip() for k in
                    ('effective_request', 'necessary_scope', 'execution_scope', 'scope_changes')), '任务背景说明缺失')
        boundary, complexity = context.get('boundary', {}), context.get('task_complexity', {})
        require(boundary.get('status') in {'consistent', 'uncertain', 'needs_correction'} and boundary.get('reason'), '缺任务边界核对')
        require(complexity.get('status') in {'ordinary', 'complex', 'unknown'} and complexity.get('reason'), '缺任务条件判断')
        require(context.get('evidence') and boundary.get('evidence') and complexity.get('evidence'), '背景判断缺来源')
        for pid in context.get('period_ids', []):
            require(pid in periods, '背景引用未知期间')
            seen.append(pid)
            for refs in (context['evidence'], boundary['evidence'], complexity['evidence']):
                for ref in refs:
                    find_proof(ref, events, periods[pid])
    require(set(seen) == set(periods) and len(seen) == len(periods), '任务背景每期间恰一次')
    return OLD_CHECK(unit, value)


def packet(data, key):
    unit, pack, events = OLD_PACKET(data, key)
    raw = pack['raw_material']
    # 仅解析原始协议标记，便于定位调用及结果；不解释工具用途，不赋难度。
    columns = raw['context_columns']
    event_column = columns.index('event')
    for row in raw['context_index']:
        text = events[row[event_column]]['text']
        name = re.search(r'^Tool: ([^\n]+)', text)
        call = re.search(r'^Call ID: ([^\n]+)', text, re.M)
        row.extend([name.group(1) if name else None, call.group(1) if call else None])
    raw['context_columns'] = columns + ['tool_name', 'call_id']
    return unit, pack, events


def activate():
    """独立CLI进程内切换版本和评审入口；旧文件及其SHA保持不变。"""
    if batch.VERSION == VERSION:
        return
    require(batch.VERSION == OLD_VERSION, '已有其他运行版本，禁止混用')
    for module in (batch, serial, base, scoring):
        module.VERSION = VERSION
    rubric = copy.deepcopy(scoring.RUBRIC)
    rubric['version'] = VERSION
    rubric['contextual_review'] = GUIDANCE
    scoring.RUBRIC = batch.RUBRIC = rubric
    batch.EXECUTION_VERSION = EXECUTION_VERSION
    batch.INSTRUCTIONS += GUIDANCE
    batch.check_review = serial.check_review = check_review
    batch.packet = packet


def prepare_dataset(source, target):
    data = base.read(source)
    require(data['policy_version'] == OLD_VERSION, '须从冻结v11索引创建v12索引')
    for ref in list(data['source_groups'].values()) + data.get('frozen_files', []):
        require(base.sha(ref['path']) == ref['sha256'], '旧来源或冻结规则变化')
    data['policy_version'] = VERSION
    data['baseline'] = dict(path=str(Path(source).resolve()), sha256=base.sha(source), policy_version=OLD_VERSION)
    # 保留旧规则来源以便追溯，新版执行模块由运行manifest冻结。
    base.write(target, data)
    return dict(dataset=str(Path(target).resolve()), sha256=base.sha(target), tasks=len(data['tasks']))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'prepare':
        parser = argparse.ArgumentParser()
        parser.add_argument('command')
        parser.add_argument('--source', required=True)
        parser.add_argument('--output', required=True)
        args = parser.parse_args()
        print(json.dumps(prepare_dataset(args.source, args.output), ensure_ascii=False))
        return
    # 续跑start必须进入本模块，避免派发到旧CLI而恢复旧评分规则。
    require('start' not in sys.argv, 'v12直接使用run执行/接续；不要调用旧start入口')
    require('--reuse-from' not in sys.argv, '语义规则已变化，禁止直接复用旧评分')
    activate()
    batch.main()


if __name__ == '__main__':
    main()
