"""面向读者的评分解释与原文案例；数值来自汇总，不用模板重判语义。"""

from collections import Counter
from datetime import timedelta
from html import escape
import re

from .common import moment
from .policy import DIMENSIONS, ITEMS, VERSION


def safe_quote(text):
    """分享报告隐藏常见凭据；私有原文与偏移仍保存在冻结输入中。"""
    text = re.sub(r'(?i)((?:password|passwd|api[_-]?key|access[_-]?token|token|contentKey|secret|密码)\s*[=:：]\s*)[^\s,;，；&#)]+', r'\1[已隐藏]', text)
    text = re.sub(r'(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+', r'\1[已隐藏]', text)
    text = re.sub(r'\bsk-[A-Za-z0-9_-]{16,}', '[已隐藏密钥]', text)
    return text


def period_label(period):
    start = moment(period["start"])
    end = moment(period["end"]) - timedelta(microseconds=1)
    label = f"{start.month}月{start.day}日—{end.month}月{end.day}日"
    if period["kind"] == "monthly":
        return f"{start.year}年{start.month}月月评"
    return label + ("（周内片段）" if not period.get("complete_week", True) else "")


def attach_reader_evidence(summary, packets, reviews, results, *, case_selections=None):
    grouped = {}
    for result in results:
        grouped.setdefault((result["user_id"], result["period_id"]), []).append(result)
    for row in summary:
        group = grouped.get((row["user_id"], row["period"]["period_id"]), [])
        own = {eid: e for r in group for e in packets[r["packet_id"]]["events"]
               for eid in [e["event_id"]] if eid in packets[r["packet_id"]]["active_human_event_ids"]}
        row["usage_counts"] = {"human_inputs": len(own), "recorded_days": len({moment(e["at"]).date() for e in own.values()}),
                               "parent_goals": len({r["goal_id"] for r in group}),
                               "meaning": "按当前来源及目标计划归类的本人输入记录；待核来源仍可能影响条数，不等于平台全部请求或独立人工轮数；记录天数不参与计分"}
        row['review_coverage'] = {
            'packets': len(group),
            'fully_read_packets': sum(r.get('semantic_complete', False) for r in group),
            'scored_cells': sum(c['status'] == 'scored' for r in group for c in r['cells']),
            'unknown_cells': sum(c['status'] in {'unobservable', 'attribution_unclear', 'pending_review', 'review_failed'} for r in group for c in r['cells']),
            'meaning': '目标期间包的阅读覆盖；周/月分别计，不是独立任务数或能力置信度',
        }
        row["reader_items"] = {}
        for item in ITEMS:
            pairs = [(r, c) for r in group for c in r["cells"] if c["item"] == item]
            scored = sorted([(r, c) for r, c in pairs if c["status"] == "scored"], key=lambda rc: (rc[1]["band"], rc[0]["packet_id"]))
            choices = scored[:1]
            if scored and scored[-1][1]["band"] != scored[0][1]["band"]:
                choices += scored[-1:]
            requested = [s for s in (case_selections or {}).get(row['user_id'], [])
                         if s['item'] == item and s['period_id'] == row['period']['period_id']]
            chosen_ids = {r['packet_id'] for r, _ in choices}
            for selected in requested:
                match = next(((r, c) for r, c in scored if r['packet_id'] == selected['packet_id']), None)
                if match and match[0]['packet_id'] not in chosen_ids:
                    choices.append(match)
                    chosen_ids.add(match[0]['packet_id'])
            cases = []
            for result, cell in choices:
                review = reviews[result["packet_id"]]
                source = {e["event_id"]: e for e in packets[result["packet_id"]]["events"]}
                proof_map = {e["id"]: e for e in review["evidence"]}
                original = next(c for c in review["cells"] if c["item"] == item)
                observations = [o for o in review["observations"] if o["id"] in original["observation_ids"]]
                own_proofs = list(dict.fromkeys(p for o in observations for p in o["evidence_ids"]))
                feedback = original["feedback_evidence_ids"]
                proof_ids = list(dict.fromkeys(own_proofs[:2] + feedback[:1])) if feedback else own_proofs[:3]
                proofs = []
                # 代表性摘录不等于全部评分依据；完整证据在已提交评审中。
                for pid in proof_ids:
                    proof = proof_map[pid]
                    event = source[proof["event_id"]]
                    raw = event["text"][proof["start"]:proof["end"]]
                    quote = safe_quote(raw[:800])
                    proofs.append({"event_id": proof["event_id"], "start": proof["start"], "end": proof["end"],
                                   "at": event["at"], "kind": proof["kind"], "quote": quote,
                                   "role": 'feedback' if pid in feedback else 'action',
                                   "excerpted": len(raw) > 800, "redacted": quote != raw[:800],
                                   "limitation": proof.get("limitation", "")})
                cases.append({"packet_id": result["packet_id"], "goal_id": result["goal_id"], "title": safe_quote(result["title"]),
                              "band": cell["band"], "reason": safe_quote(cell["reason"]),
                              "facts": [safe_quote(o["fact"]) for o in observations], "evidence": proofs,
                              "complex_weight": cell.get("weight_exact", "1"),
                              "upper_unobserved": cell.get("unobserved_upper_anchors", [])})
            missing = Counter(c["status"] for _, c in pairs if c["status"] != "scored")
            row["reader_items"][item] = {"cases": cases, "other_states": dict(missing),
                "limitations": list(dict.fromkeys(safe_quote(c["reason"]) for _, c in pairs if c["status"] != "scored"))[:3]}


def render_analysis_report(data, output):
    title = "智用力评测报告"
    state = "试评执行已闭合，尚未真人校准" if data["trial_complete"] else "诊断草稿：评审或复核尚未闭合"
    if not data.get("participation_status", {}).get("complete"):
        state += "；参与材料未齐，候选月级暂不确定"
    if data.get("preview_notice"):
        state = data["preview_notice"]
    blocks, md = [], ["# " + title, "", state, ""]

    def paragraph(text):
        return "<p>" + escape(str(text)) + "</p>"

    def section(role, heading, content):
        blocks.append(f'<section data-contract-section="{role}"><h2>{escape(heading)}</h2>{content}</section>')

    def table(headers, rows):
        return '<div class="table-wrap"><table><thead><tr>' + ''.join('<th scope="col">' + escape(str(h)) + '</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + escape(str(c)) + '</td>' for c in row) + '</tr>' for row in rows) + '</tbody></table></div>'

    def md_table(headers, values):
        def clean(c): return str(c).replace('|', '\\|').replace('\n', ' ')
        md.extend(['', '|' + '|'.join(headers) + '|', '|' + '|'.join('---' for _ in headers) + '|'] + ['|' + '|'.join(map(clean, row)) + '|' for row in values] + [''])

    def rating(row):
        grade = row.get('rating') or row.get('trial_rating')
        return grade['id'] + ' ' + grade['name'] + ('（观察试评）' if not row.get('rating') else '') if grade else row['rating_status']

    def score(row):
        return row["overall_score"] if row["overall_score"] is not None else "未形成完整四维分"

    summary_text = [state + "。本报告不作员工排名。",
        "每周一评价上一自然周；月初对上一自然月重评。完整周与周内片段分别标明，月分不平均周分。",
        "提示词质量、任务驾驭度、判断与核验、使用效率各占表现分25%。月度另列90%表现＋10%参与的候选综合及月级，供本轮验证。",
        "记录天数少不直接扣分。未观察、未评完和未使用分别解释；没有充分依据时不补零或编造评级。"]
    section('executive-summary', '评测摘要', '<div class="callout">' + ''.join(paragraph(t) for t in summary_text) + '</div>')
    md.extend(['## 评测摘要', '', *summary_text, ''])

    scope = "周窗为上海时区周一00:00至下周一00:00，日期按含首尾日展示。周末之后的证据不倒灌；首尾片段不冒充完整周。原四维表现分S保留，参与度A按有实质使用的周窗在本月所占时长计算。无机会不补分，未知会限制结果。"
    section('metric-definitions', '先读懂分数与统计范围', paragraph(scope) + paragraph("复杂条件确实形成本人处理机会时，仅对应任务驾驭子项采用1.5权重，普通机会为1；正反表现同等处理，不另加奖励分。L1基础＜50，L2稳健50—＜70，L3良好70—＜85，L4优秀85—＜95，L5卓越95—100。"))
    md.extend(['## 统计口径', '', scope, ''])
    months = [r for r in data["summary"] if r["period"]["kind"] == "monthly"]
    headers = ['用户 / 月份', '四维表现S', '参与度A', '候选综合', '候选月级']
    values = [[r['user_id'] + ' / ' + period_label(r['period']), score(r), r.get('participation_score') or '待核', r.get('monthly_candidate_score') or '待核', rating(r)] for r in months]
    section('key-findings', '月度结果：表现与持续使用分别可见', paragraph("候选综合不是周分平均值。它在整月表现基础上体现是否持续实质使用；10%为本轮产品候选，不能当作已验证的量表结论。") + table(headers, values))
    md.extend(['## 月度结果', '']); md_table(headers, values)

    users = list(dict.fromkeys(r['user_id'] for r in data['summary']))
    for user in users:
        rows = [r for r in data['summary'] if r['user_id'] == user]
        weekly = [r for r in rows if r['period']['kind'] == 'weekly']
        wh = ['统计区间', '计划周一出分日', '本人输入 / 天数 / 目标', '四维表现分', '用量及材料说明']
        wv = []
        for row in weekly:
            n, u = row.get('usage_counts', {}), row.get('usage', {})
            activity = {'substantive': '有实质使用', 'limited': '仅零星尝试', 'none': '确认未使用', 'unknown': '使用情况待核'}.get(u.get('activity'), '使用情况待核')
            description = activity + '；' + safe_quote(u.get('reason', '待核'))
            if row['overall_score'] is None:
                description += '；缺整维可评材料，不能合成周分；各项已证表现仍保留'
            if not row['period'].get('complete_week', True):
                description += '；片段观察，待整周补齐后发布周分'
            wv.append([period_label(row['period']), row['period'].get('publish_on', '待核'),
                       f"{n.get('human_inputs', 0)} / {n.get('recorded_days', 0)} / {n.get('parent_goals', 0)}", score(row), description])
        content = paragraph("天数与数量仅用于理解材料规模，不参与四维计分。以下逐期解释与案例均来自本期已提交评审；代表案例不是唯一评分样本。") + table(wh, wv)
        md.extend(['## ' + user, '']); md_table(wh, wv)
        for row in sorted(rows, key=lambda r: r['period']['kind'] != 'monthly'):
            label = period_label(row['period'])
            counts = row.get('usage_counts', {})
            lead = f"{label}：四维表现 {score(row)}；本人输入 {counts.get('human_inputs', 0)} 条，记录 {counts.get('recorded_days', 0)} 天，父目标 {counts.get('parent_goals', 0)} 个。"
            detail = paragraph(lead)
            md.extend(['### ' + label, '', lead, ''])
            for dimension, spec in DIMENSIONS.items():
                info = row['dimensions'][dimension]
                head = spec['name'] + '：' + (info['observed_score'] or '暂无完整观察') + f"；已评分子项 {info['covered_items']}/{info['applicable_items']} 项适用"
                detail += '<h4>' + escape(head) + '</h4>'
                md.extend(['#### ' + head, ''])
                for item in spec['items']:
                    item_info = row['items'][item]
                    reader = row.get('reader_items', {}).get(item, {})
                    text = f"{ITEMS[item]['name']}：{item_info['observed_score'] or '未评分'}。有分目标 {item_info['scored_goals']}/{item_info['active_goals']}，未知 {item_info['unknown_goals']}。"
                    detail += paragraph(text); md.extend([text, ''])
                    cases = reader.get('cases', [])
                    for case in cases:
                        line = f"案例「{case['title']}」：{case['band']}档。{case['reason']}"
                        detail += '<article class="case">' + paragraph(line)
                        md.extend([line, ''])
                        for proof in case['evidence']:
                            tag = {'person_input':'本人输入', 'person_action':'本人行动记录', 'person_report':'本人反馈', 'artifact':'成果材料', 'verification_result':'核验记录', 'assistant_claim':'AI自述', 'context':'上下文'}.get(proof['kind'], proof['kind'])
                            note = str(proof['at'])[:10] + ' · ' + tag + (' · 节选' if proof['excerpted'] else '') + (' · 凭据已隐藏' if proof['redacted'] else '')
                            detail += '<blockquote><p>' + escape(proof['quote']) + '</p><footer>' + escape(note) + '</footer></blockquote>'
                            if proof['limitation']:
                                detail += paragraph('证据边界：' + proof['limitation'])
                            md.extend(['> ' + proof['quote'].replace('\n', '\n> '), '', note, ''])
                        if case['upper_unobserved']:
                            note = '更高档所需证据尚未观察到，不等于已证能力不足。后续在适用机会中可关注：' + ITEMS[item]['reasoned']
                            detail += paragraph(note); md.extend([note, ''])
                        detail += '</article>'
                    for limitation in reader.get('limitations', []):
                        detail += paragraph('材料或适用性说明：' + limitation); md.extend(['材料或适用性说明：' + limitation, ''])
            cm = '复杂任务：本期确认C2目标 ' + str(row['complex_goals']) + ' 个；复杂机会M观察分 ' + (row['observed_complex_m_score'] or '未形成') + '；M普通等权基线 ' + (row['dimensions']['M']['unweighted_score'] or '未形成') + '，加权后 ' + (row['dimensions']['M']['observed_score'] or '未形成') + '。缺复杂机会不限制等级。'
            detail += paragraph(cm); md.extend([cm, ''])
            if row['period']['kind'] == 'monthly':
                content += '<h3>' + escape(label) + '</h3>' + detail
            else:
                content += '<details><summary>' + escape(label + '：评分原因与实际案例') + '</summary>' + detail + '</details>'
        section('person-results', user + '：周分、月级与评分依据', content)

    recommendations = '优先完成来源与父目标核对、全量语义评审及独立复核，再确认参与判断。对有据的必要缺口按案例提出改进行动；仅缺材料时补证，不把补证写成能力缺陷。'
    questions = '需继续核对：低用量周是否真无实质使用，还是采集缺失、非评价时段或未评完？90%表现＋10%参与的候选结果是否符合实际案例？这些问题未核清前不作正式员工排名。'
    caveats = '本报告为本地试评。程序核验引用与计算，不证明语义结论正确；多Agent一致不代替真人校准。跨岗位公平性及生产发布尚未验收。旧批次若存在机械80分或来源污染，其数值只能用于诊断。'
    for role, heading, text in [('recommended-next-steps', '下一步：先处理已证问题，再确认月级', recommendations), ('further-questions', '需要核对的事项', questions), ('caveats-and-assumptions', '结果的适用边界', caveats)]:
        section(role, heading, paragraph(text)); md.extend(['## ' + heading, '', text, ''])
    css = 'body{margin:0;background:#f3f5f7;color:#172033;font:15px/1.75 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}main{max-width:1080px;margin:auto;background:white;padding:44px 48px}h1{font-size:32px;margin:0 0 24px}h2{font-size:23px;margin:34px 0 14px}h3{font-size:19px;margin-top:28px}h4{font-size:16px;margin:24px 0 10px}p{margin:9px 0}section{margin-bottom:36px}.callout{background:#edf3fa;border-left:4px solid #315f94;padding:16px 22px}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:11px 12px;border-bottom:1px solid #dce2e9;text-align:left;vertical-align:top}th{background:#eef2f6;font-weight:600}td{min-width:65px}.case{border-left:3px solid #d9e3ef;padding-left:18px;margin:18px 0}blockquote{margin:12px 0;background:#f7f9fb;padding:12px 16px;white-space:pre-wrap;overflow-wrap:anywhere}blockquote p{margin:0}footer{font-size:12px;color:#536171}details{margin:16px 0;border-top:1px solid #dce2e9;padding-top:12px}summary{cursor:pointer;font-weight:600}small{color:#536171}@media print{body{background:white}main{padding:0;max-width:none}details{display:block}details>summary{font-weight:bold}details>*{display:block}h2,h3,h4{break-after:avoid}article,blockquote,tr{break-inside:avoid}thead{display:table-header-group}table{font-size:10px}.table-wrap{overflow:visible}}'
    page = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + title + '</title><style>' + css + '</style></head><body><main data-report-audience="product stakeholders"><header data-contract-section="title"><h1>' + title + '</h1></header>' + ''.join(blocks) + '<footer>评分版本 ' + escape(data.get('policy_version', VERSION)) + ' · ' + escape(state) + '</footer></main></body></html>'
    (output / 'report.html').write_text(page, encoding='utf-8')
    (output / 'report.md').write_text('\n'.join(md) + '\n', encoding='utf-8')


def public_cases(rows, limit=3):
    """少量展示已记录的事实；优先已证缺口、复杂机会及整月判断，不重算评分。"""
    candidates = [(row, item, case) for row in rows for item, info in row.get('reader_items', {}).items() for case in info['cases']]
    candidates.sort(key=lambda x: (x[2]['band'] > 60, x[2].get('complex_weight') != '3/2',
                                  x[0]['period']['kind'] != 'monthly', x[2]['band'], x[2]['packet_id'], x[1]))
    chosen, goals = [], set()
    for candidate in candidates:
        if candidate[2]['goal_id'] in goals:
            continue
        chosen.append(candidate)
        goals.add(candidate[2]['goal_id'])
        if len(chosen) == limit:
            break
    return chosen


def render_report(data, output):
    """对外正文简洁展示，完整审查记录存入独立的内部分析目录。"""
    analysis = output / 'analysis'
    analysis.mkdir(exist_ok=True)
    render_analysis_report(data, analysis)
    title, parts, md = '智用力评测报告', [], ['# 智用力评测报告', '']

    def para(text):
        md.extend([str(text), ''])
        return '<p>' + escape(str(text)) + '</p>'

    def section(role, heading, body):
        parts.append(f'<section data-contract-section="{role}"><h2>{escape(heading)}</h2>{body}</section>')

    def table(headers, values):
        clean = lambda text: str(text).replace('|', '\\|').replace('\n', ' ')
        md.extend(['|' + '|'.join(headers) + '|', '|' + '|'.join('---' for _ in headers) + '|',
                   *['|' + '|'.join(clean(c) for c in row) + '|' for row in values], ''])
        return '<div class="table-wrap"><table><thead><tr>' + ''.join('<th>' + escape(h) + '</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + escape(str(c)) + '</td>' for c in row) + '</tr>' for row in values) + '</tbody></table></div>'

    def value(number):
        return number if number is not None else '—'

    months = [r for r in data['summary'] if r['period']['kind'] == 'monthly']
    notice = data.get('preview_notice') or ('本次为试评结果。' if data.get('report_complete') else '部分结果待核实，尚未确定的分数和等级以“—”标示。')
    md.extend(['## 评测摘要', ''])
    body = para(notice)
    body += para('每周评价当周表现，月度结合整月表现与持续使用情况评级。记录天数少不直接扣分，评分依据为实际任务中的本人行为。')
    section('executive-summary', '评测摘要', body)
    md.extend(['## 月度结果', ''])
    body = para('月度综合由整月表现占90%、使用参与度占10%形成；本轮为试评口径。完整周与周内片段分别标注。')
    body += table(['用户', '月份', '已观察表现¹', '使用参与度', '综合参考¹', '月度等级', '全文阅读目标'],
        [[r['user_id'], period_label(r['period']), value(r['overall_score']), value(r.get('participation_score')),
          value(r.get('monthly_candidate_score')), ((r.get('rating') or r.get('trial_rating'))['id'] + ' ' + (r.get('rating') or r.get('trial_rating'))['name']
              + ('（观察试评）' if not r.get('rating') else '')) if r.get('rating') or r.get('trial_rating') else '暂未形成',
          str(r.get('review_coverage', {}).get('fully_read_packets', 0)) + '/' + str(r.get('review_coverage', {}).get('packets', 0))] for r in months])
    body += para('¹ 本轮尚未闭合时，数字仅汇总有依据的已评分部分；剩余未知部分未补分，不能作为完整月分或人员排名。全文阅读数是材料覆盖说明，不是扣分项。')
    if data.get('participation_note'):
        body += para(data['participation_note'])
    section('key-findings', '月度结果', body)

    for user in dict.fromkeys(r['user_id'] for r in data['summary']):
        user_rows = [r for r in data['summary'] if r['user_id'] == user]
        md.extend(['## ' + user, ''])
        values = []
        for row in user_rows:
            counts = row.get('usage_counts', {})
            activity = {'substantive': '有实质使用', 'limited': '零星尝试', 'none': '本平台未观察到使用', 'unknown': '使用情况待核'}.get(row.get('usage', {}).get('activity'), '使用情况待核')
            if row['period']['kind'] == 'monthly':
                activity = '整月评价' if row.get('evaluation_complete') else ('整月材料已评，缺证单列' if row.get('semantic_review_complete') else '月评尚待核实')
            elif row['overall_score'] is None and counts.get('human_inputs', 0):
                missing = [spec['name'] for d, spec in DIMENSIONS.items() if row['dimensions'][d]['observed_score'] is None]
                activity += '；缺' + '、'.join(missing) + '的可评材料'
            coverage = row.get('review_coverage', {})
            values.append([period_label(row['period']), *[value(row['dimensions'][d]['observed_score']) for d in DIMENSIONS],
                           value(row['overall_score']), f"{counts.get('human_inputs', '—')}条 / {counts.get('recorded_days', '—')}天",
                           str(coverage.get('fully_read_packets', 0)) + '/' + str(coverage.get('packets', 0)), activity])
        body = table(['期间', '提示词', '驾驭', '核验', '效率', '观察分¹', '记录用量', '全文阅读目标', '说明'], values)
        body += para(data.get('reader_explanations', {}).get(user, '本期数字仅说明已有证据支持的表现；未闭合部分另列，不把评审进度当成个人能力。'))
        # 维度解释只引用已有本期结论；月度没有判断时不把周度案例拼成月度结论。
        month = next((r for r in user_rows if r['period']['kind'] == 'monthly'), None)
        if month and not data.get('curated_cases') and any(i['cases'] for i in month.get('reader_items', {}).values()):
            body += '<h3>主要评分依据</h3>'; md.extend(['### 主要评分依据', ''])
            for d, spec in DIMENSIONS.items():
                candidates = [(item, c) for item in spec['items'] for c in month.get('reader_items', {}).get(item, {}).get('cases', [])]
                if candidates:
                    item, case = min(candidates, key=lambda x: x[1]['band'])
                    body += para(spec['name'] + '：' + case['reason'] + '（对应“' + ITEMS[item]['name'] + '”子项。）')
        else:
            body += para('下列案例说明对应子项的实际表现；月分由整月全部有依据的目标轨迹汇总。' if month and month.get('semantic_review_complete') else '整月评审尚未闭合；下列案例只说明对应的具体表现。')
        selection = data.get('curated_cases', {}).get(user)
        if selection is None:
            cases = public_cases(user_rows)
        else:
            cases = []
            for selected in selection:
                row = next(r for r in user_rows if r['period']['period_id'] == selected['period_id'])
                case = next(c for c in row['reader_items'][selected['item']]['cases'] if c['packet_id'] == selected['packet_id'])
                if selected.get('reader_reason'):
                    case = dict(case, reason=selected['reader_reason'])
                cases.append((row, selected['item'], case))
        if cases:
            body += '<h3>事实案例</h3>'; md.extend(['### 事实案例', ''])
        for row, item, case in cases:
            body += '<article class="case">' + para(period_label(row['period']) + ' · ' + ITEMS[item]['name'] + ' · ' + str(case['band']) + '档')
            body += para('任务：' + case['title']) + para('评分理由：' + case['reason'])
            # 保留本人表达与独立反馈各一段，避免只有结论没有事实，也避免堆原文。
            persons = [p for p in case['evidence'] if p['kind'].startswith('person_') and p.get('role') != 'feedback']
            feedback = next((p for p in case['evidence'] if p.get('role') == 'feedback' and p['kind'] in {'person_report', 'person_action', 'artifact', 'verification_result'}), None)
            shown = persons[:1 if feedback else 2] + ([feedback] if feedback else [])
            seen_quotes, limits = set(), []
            for proof in shown:
                if proof['quote'] in seen_quotes:
                    continue
                seen_quotes.add(proof['quote'])
                label = proof.get('reader_label') or ('对应反馈' if proof is feedback else '本人原句')
                note = str(proof['at'])[:10] + ' · ' + label
                body += '<blockquote>' + escape(proof['quote']) + '<footer>' + escape(note) + '</footer></blockquote>'
                md.extend(['> ' + proof['quote'].replace('\n', '\n> '), '', note, ''])
                if proof['limitation'] and proof['limitation'] not in limits:
                    limits.append(proof['limitation'])
            for limitation in limits:
                body += para('证据范围：' + limitation)
            body += '</article>'
        section('person-results', user + '：分数与事实依据', body)

    md.extend(['## 阅读说明', ''])
    body = para('四维表现各占25%；月度分数重新汇总，不平均周分。等级为L1基础、L2稳健、L3良好、L4优秀、L5卓越，分界为50、70、85、95分。')
    body += para('复杂关系确实形成本人驾驭机会时，对应M子项按1.5倍权重纳入，普通机会为1；正反表现均同样处理，不另加亮点奖励分。')
    body += para('案例用于说明具体行为，案例档位不等于完整任务分。材料不足会保留待核状态；试评结果仅反映本平台当期可观察的使用表现。')
    body += para('记录用量仅统计本批归入目标的输入记录，来源待核项仍可能影响条数，不代表平台全部调用量；条数与天数不参与四维计分。')
    body += para(data.get('reader_execution_note') or '本稿按已记录的阅读、评分与复核状态出具；材料缺证与尚未完成评审分别保留。')
    section('caveats-and-assumptions', '阅读说明', body)
    css = 'body{margin:0;background:#f5f6f8;color:#172033;font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}main{max-width:1120px;margin:auto;background:white;padding:36px 40px}h1{font-size:30px}h2{font-size:21px;margin-top:30px}h3{font-size:16px}p{margin:10px 0}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:9px;border-bottom:1px solid #dce2e9;text-align:left;vertical-align:top}th{background:#f0f3f7}.table-wrap{overflow:auto}.case{margin:16px 0;padding-left:16px;border-left:3px solid #d9e3ef}blockquote{margin:10px 0;background:#f7f9fb;padding:10px 14px;white-space:pre-wrap;overflow-wrap:anywhere}footer{color:#536171;font-size:12px}@media print{body{background:white}main{padding:0;max-width:none}.table-wrap{overflow:visible}table{font-size:10px}tr,blockquote{break-inside:avoid}h2,h3{break-after:avoid}thead{display:table-header-group}}'
    page = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + title + '</title><style>' + css + '</style></head><body><main data-report-audience="product stakeholders"><header data-contract-section="title"><h1>' + title + '</h1></header>' + ''.join(parts) + '</main></body></html>'
    (output / 'report.html').write_text(page, encoding='utf-8')
    (output / 'report.md').write_text('\n'.join(md) + '\n', encoding='utf-8')
