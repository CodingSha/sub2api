"""冻结、引用和时间的确定性校验。"""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

from .policy import KINDS


class ContractError(ValueError):
    """拒绝错误合同，不把评审失败变成人员低分。"""


def require(condition, message):
    if not condition:
        raise ContractError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def rows(path):
    with Path(path).open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def write_rows(path, values):
    with Path(path).open("w", encoding="utf-8") as target:
        for value in values:
            target.write(json.dumps(value, ensure_ascii=False) + "\n")


def moment(value):
    try:
        value = value.replace("Z", "+00:00")
        value = re.sub(r"(\d{2}:\d{2}:\d{2})\.(\d{1,9})(?=[+-]\d{2}:\d{2}$)",
                       lambda m: m[1] + "." + m[2][:6].ljust(6, "0"), value)
        result = datetime.fromisoformat(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ContractError("时间必须是带时区ISO8601：" + str(value)) from exc
    require(result.tzinfo is not None, "时间缺少时区")
    return result


def display(value):
    if value is None:
        return None
    number = Decimal(value.numerator) / Decimal(value.denominator)
    return str(number.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def valid_span(text, start, end):
    return type(start) is int and type(end) is int and 0 <= start < end <= len(text)


def code_hashes():
    return {p.name: digest(p.read_bytes()) for p in sorted(Path(__file__).parent.glob("*.py"))
            if not p.name.startswith("test_")}


def copy_snapshot(source, destination):
    """APFS上使用独立的写时复制快照，避免新批次重复占用数GB；不使用共享硬链接。"""
    if sys.platform == "darwin":
        result = subprocess.run(["/bin/cp", "-c", str(source), str(destination)], capture_output=True)
        if result.returncode == 0:
            return str(destination)
    return shutil.copyfile(source, destination)


def review_context(value):
    require(isinstance(value, dict) and all(isinstance(value.get(k), str) and value[k].strip()
            for k in ("team_id", "agent_id", "context_id")), "缺实际team_id/agent_id/context_id")
    return value


def injection_hints(text):
    """只产生来源复核候选，不按词表决定是否本人内容。引用这些字符串也会命中。"""
    markers = ("<appshot>", "Automation:", "Background subagent", "<goal_round>",
               "[model changed:", "Memory Writing Agent", "Generate 0 to 3 hyperpersonalized",
               "You are a summarization agent", "[IMPORTANT: You are running as a scheduled cron job",
               "【流水线后台完成】", "Review the conversation above and update the skill library")
    return [marker for marker in markers if marker in text]


class Evidence:
    def __init__(self, packet, submitted):
        self.packet = packet
        self.events = {e["event_id"]: e for e in packet["events"]}
        self.materials = {m["event_id"]: m for m in packet["materials"]}
        self.records = {}
        for record in submitted:
            key = record["id"]
            require(key not in self.records, "证据ID重复")
            require(record["event_id"] in self.events, "跨包、未来或未知证据")
            event = self.events[record["event_id"]]
            a, b = record["start"], record["end"]
            require(valid_span(event["text"], a, b), "证据片段越界或为空")
            text = event["text"][a:b]
            require(bool(text.strip()), "空白不能作为证据")
            require(record["kind"] in KINDS, "未知证据类型")
            require(record.get("quote", text) == text, "引用与原文不符")
            if event["truncated"] or event["response_ambiguous"]:
                require(bool(record.get("limitation")), "截断或来源歧义必须说明限制")
            uncertain = event.get("source_snapshot_conflict", False) or event["field"] == "response" and (event["response_ambiguous"] or event.get("response_timestamp_unknown", False))
            if uncertain:
                require(bool(record.get("limitation")), "响应配对或时间未知须说明限制")
            if record["kind"].startswith("person_"):
                material = self.materials.get(record["event_id"])
                require(material and material["classification"] == "human", "代理/系统材料不能冒充本人")
                require(any(x <= a < b <= y for x, y in material["human_spans"]), "超出当轮本人片段")
            if event["field"] == "response" and record["kind"] in {"artifact", "verification_result"}:
                require(bool(record.get("source_basis")), "助手响应标作成果/核验结果须说明实际结果来源，不能把完成自述改标签")
            self.records[key] = dict(record, quote=text, source_uncertain=uncertain)

    def refs(self, ids, *, needed=False, kinds=None):
        require(isinstance(ids, list) and all(isinstance(x, str) for x in ids), "引用须为字符串列表")
        require(len(ids) == len(set(ids)), "引用列表重复")
        require(not needed or bool(ids), "缺少具体证据")
        require(all(key in self.records for key in ids), "证据ID不存在")
        records = [self.records[key] for key in ids]
        if kinds:
            require(any(r["kind"] in kinds for r in records), "证据类型不支持该主张")
        return records

    def signature(self, record):
        return (record["event_id"], record["start"], record["end"])

    def period_human(self, event_id):
        event = self.events.get(event_id)
        material = self.materials.get(event_id)
        return bool(event and material and material["classification"] == "human"
                    and not event.get("source_snapshot_conflict", False)
                    and moment(self.packet["period_start"]) <= moment(event["at"]) < moment(self.packet["period_end"]))
