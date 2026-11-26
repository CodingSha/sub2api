"""使用效率补评：重读工具调用，只重判 E；归属变化时重判 M。提示词和判断核验沿用已发布结果。"""
import copy
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path

from daily_assessment import file_reference_only, workflow_selected_in_task
from daily_tasks import workflow_rows
from pipeline import Paused
from runtime import RUBRIC, atomic


LOGIC_FILES = ('efficiency_rereview.py', 'daily_tasks.py', 'daily_assessment.py', 'weekly_daily.py', 'weekly_scores.py')


def image_id():
    """只认本次进程或镜像内写入的标识。未锁定时不发模型请求。"""
    value = os.environ.get('ZHIYONGLI_IMAGE_ID', '').strip()
    if not value:
        path = Path('/etc/zhiyongli-image-id')
        if path.is_file():
            value = path.read_text().strip()
    if not value or value == 'unspecified':
        raise Paused('needs_review', '补评开始前没有锁定的镜像标识，不发送模型请求')
    return value


def logic_digest():
    digest = hashlib.sha256()
    root = Path(__file__).resolve().parent
    for name in LOGIC_FILES:
        digest.update(name.encode())
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


def identity(job):
    from daily_reasoning import VERSION as daily_version
    from weekly_daily import VERSION as review_version
    from weekly_scores import VERSION as aggregation
    return dict(rules='1.6', review=review_version, daily=daily_version, aggregation=aggregation,
        image=image_id(), logic=logic_digest(), scope_key=job.get('scope_key', ''),
        period_start=str(job['period_start']), period_end=str(job['period_end']),
        workday_scope=job['workday_scope'])


def require_lock(root, job):
    """同一任务目录第一次写入锁定；之后镜像、规则或日期不一致就停这条，不改本批逻辑。"""
    path = Path(root) / 'batch-lock.json'
    current = identity(job)
    if path.exists():
        saved = json.loads(path.read_text())
        if saved != current:
            raise Paused('needs_review', '本批已锁定的镜像、规则或日期与当前进程不一致，不改本批逻辑')
        return saved
    atomic(path, current)
    return current


def load_pool(root):
    pool = {}
    for name in ('source/records.json', 'records.json'):
        path = Path(root) / name
        if not path.is_file():
            continue
        loaded = json.loads(path.read_text())
        rows = loaded.values() if isinstance(loaded, dict) else loaded
        for row in rows:
            if isinstance(row, dict) and row.get('id'):
                pool[row['id']] = row
    return pool


def order_timeline(spine, tools):
    seen = {row['id'] for row in spine}
    extra = [row for row in tools if row['id'] not in seen]
    rows = list(spine) + extra
    if rows and all(row.get('at') for row in rows):
        return sorted(rows, key=lambda row: (row.get('at') or '', row['id']))
    return rows


def timelines_for(material, sources, state):
    """按任务把本人正文和工具原文排回同一条时间线。索引里的工具找不到原文就停止，不靠名称猜测。"""
    from review_control import selected
    from weekly_daily import prefix
    by_source = {}
    for source in sources:
        marker = prefix(source['period_start'])
        root = selected(Path(state) / str(source['source_job_id']) / 'review')
        pool = load_pool(root)
        for task_id, conversation in material['conversations'].items():
            if not task_id.startswith(marker):
                continue
            local_task = task_id[len(marker):]
            spine = [dict(row) for row in conversation['records']]
            indexed = {entry['record_id'] for entry in conversation.get('tool_index', [])}
            tools = []
            for row in pool.values():
                if row.get('role') != 'tool':
                    continue
                prefixed = marker + row['id']
                if prefixed in indexed or row.get('task_id') in {local_task, task_id}:
                    tools.append(dict(row, id=prefixed, task_id=task_id))
            ordered = order_timeline(spine, tools)
            present = {row['id'] for row in ordered if row.get('role') == 'tool'}
            if indexed - present:
                raise ValueError('入选任务有工具索引但找不到工具原文，不能补判使用效率')
            for row in spine:
                if row.get('role') == 'user':
                    by_source[row['id']] = ordered
    return by_source


def classify_one(behavior, quote, records):
    """P、J 保持原归属。方法分工只有在同轮命令触发了技能、MCP 或 CLI 时才改到复用自动化。"""
    item = behavior['primary_item']
    if item[:1] in 'PJ':
        return item
    names = [row['name'] for row in workflow_rows([row for row in records if row.get('role') == 'tool'])]
    triggered = any(workflow_selected_in_task(name, [behavior['source_ref']], records) for name in names)
    if item.startswith('M') and triggered:
        return 'E3'
    if item == 'E3' and file_reference_only(quote) and not triggered:
        return None
    return item


def revision_plan(material, timelines):
    behaviors = material['behaviors']
    overrides = {}
    for key, behavior in behaviors.items():
        records = timelines.get(behavior['source_ref'])
        if records is None:
            raise ValueError('已核行为缺少任务原文，不能重判使用效率')
        new_item = classify_one(behavior, material['quotes'][key], records)
        if new_item != behavior['primary_item']:
            overrides[key] = new_item
    updated = copy.deepcopy(behaviors)
    for key, item in overrides.items():
        if item is None:
            updated.pop(key, None)
        else:
            updated[key] = dict(updated[key], primary_item=item)

    def groups(rows):
        found = {}
        for ident, behavior in rows.items():
            found.setdefault(behavior['primary_item'], set()).add(ident)
        return found

    old, new = groups(behaviors), groups(updated)
    changed = {code for code in set(old) | set(new) if old.get(code) != new.get(code)}
    if any(code[:1] in 'PJ' for code in changed):
        raise ValueError('补评移动了提示词或判断核验归属')
    judge = {code for code in new if code.startswith('E')}
    judge |= {code for code in changed if code.startswith('M')}
    frozen = set(RUBRIC['items']) - judge
    for code in list(frozen):
        if code.startswith('E') and code not in new:
            frozen.discard(code)
    return overrides, judge, frozen


def published_week(db, job):
    """读取已发布周评正文，并把证据表里的依据装回分项；导入校验要求 observed 项带 evidence。"""
    row = db.execute("""SELECT id, body FROM zhiyongli_reports
        WHERE user_id=%s AND period_type='week' AND period_start=%s::date
          AND publication_status='published'
        ORDER BY revision DESC LIMIT 1""", (job['user_id'], str(job['period_start']))).fetchone()
    if not row:
        raise Paused('needs_review', '使用效率补评找不到已发布周评，不新造分数')
    report_id, body = row[0], row[1]
    if isinstance(body, str):
        body = json.loads(body)
    if not isinstance(body, dict) or not isinstance(body.get('items'), list):
        raise Paused('needs_review', '已发布周评正文无法读取，不新造分数')
    evidence_rows = db.execute("""SELECT item_code, body FROM zhiyongli_evidence
        WHERE report_id=%s ORDER BY item_code, evidence_key""", (report_id,)).fetchall()
    by_code = {}
    for row in evidence_rows:
        # psycopg dict_row 不能用 for a,b in rows 解包，那会拿到字段名而不是值。
        code, raw = row[0], row[1]
        value = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
        by_code.setdefault(code, []).append(value)
    for item in body['items']:
        code = item.get('item_code') or item.get('code')
        if code in by_code:
            item['evidence'] = copy.deepcopy(by_code[code])
    return body


def dimensions_of(items):
    from scoring_pipeline.weekly_performance.scores022 import packed
    dimensions = []
    for code, name in zip('PMJE', ('提示词质量', '任务驾驭度', '判断核验', '使用效率')):
        known = [item for item in items if item['item_code'][:1] == code and item.get('exact') is not None]
        gaps = [Fraction(item['exact']) for item in known if item.get('necessary_gap')]
        value = min(gaps) if gaps else sum((Fraction(item['exact']) for item in known), Fraction()) / len(known) if known else None
        dimensions.append(dict(code=code, name=name, **packed(value)))
    return dimensions


def merge_frozen(result, prior, frozen_codes):
    """已冻结分项沿用发布正文；本次重判的分项替换后重算维度和周分。"""
    from weekly_scores import apply as apply_weekly_score
    prior_items = {item['item_code']: item for item in prior['items']}
    merged = []
    for item in result['items']:
        code = item['item_code']
        if code in frozen_codes:
            if code not in prior_items:
                raise ValueError('已发布周评缺少需保留的分项')
            merged.append(copy.deepcopy(prior_items[code]))
        else:
            merged.append(item)
    result['items'] = merged
    result['dimensions'] = dimensions_of(merged)
    apply_weekly_score(result)
    if result.get('score_status') != 'no_activity' and not any(item.get('exact') for item in result['items']):
        raise ValueError('补评后仍没有可计分分项')
    return result


def run(db, sources, job, state, root, client):
    from weekly_daily import disjoint_evidence, evaluate, prepared
    root = Path(root)
    review = root / 'review'
    require_lock(root, job)
    if (root / 'repair-exhausted.json').exists():
        raise Paused('needs_review', '使用效率补评的事实或合同修复已用尽，本人口径暂停并保留回执，其他人员继续')
    prior = published_week(db, job)
    if prior.get('score_status') == 'no_activity':
        raise Paused('no_activity', '该用户本周没有个人行为，不进入使用效率补评，原0分保持发布')
    material = prepared(sources, job, state)
    overrides, judge_codes, frozen = revision_plan(material, timelines_for(material, sources, state))
    try:
        bundle = evaluate(sources, job, state, review, client, primary_overrides=overrides, judge_codes=judge_codes)
    except ValueError as ex:
        if isinstance(ex, Paused):
            raise
        if any(review.rglob('*.receipt.json')):
            atomic(root / 'repair-exhausted.json', dict(at=datetime.now(timezone.utc).isoformat(), error_type=type(ex).__name__))
        raise
    if len(bundle['reports']) != 1:
        raise ValueError('使用效率补评返回多份报告，禁止猜测正式结果')
    merge_frozen(bundle['reports'][0], prior, frozen)
    disjoint_evidence(bundle['reports'][0]['items'])
    atomic(review / 'report.json', bundle['reports'][0])
    return bundle
