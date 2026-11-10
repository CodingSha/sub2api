"""识别包装中的当轮请求候选；结构线索不自动证明本人身份。"""

import json
import re

from .common import require


IDE_HEADERS = (
    '<in-app-browser-context source="ambient-ui-state">',
    '# Applications mentioned by the user:',
    '# Context from my IDE setup:',
    '# Files mentioned by the user:',
)


def current_request_candidates(text):
    """只识别外层已知包装，不从标题/摘要里的历史引文重提本人消息。"""
    if text.startswith(IDE_HEADERS):
        markers = list(re.finditer(r'^## My request:[ \t]*\n?', text, re.M))
        if markers:
            start, end = markers[-1].end(), len(text)
            if text[start:end].strip():
                return [{'kind': 'ide_current_request', 'span': [start, end]}]
    if text.startswith('# lark-channel-bridge 运行约定'):
        messages = list(re.finditer(r'^## user_message\s*$', text, re.M))
        if not messages:
            return []
        section = text[messages[-1].end():]
        context = re.search(r'<bridge_context>\s*(\{.*?\})\s*</bridge_context>', section, re.S)
        current = re.search(r'<user_input>(.*?)</user_input>', section, re.S)
        if context and current:
            try:
                is_user = json.loads(context[1]).get('senderType') == 'user'
            except ValueError:
                is_user = False
            if is_user and current[1].strip():
                offset = messages[-1].end()
                return [{'kind': 'bridge_current_request',
                         'span': [offset + current.start(1), offset + current.end(1)]}]
    return []


def validate_current_request_disposition(text, material):
    """阻止以平台前缀为由整段丢弃真实请求；不确定必须显式保留。"""
    candidates = current_request_candidates(text)
    if not candidates:
        return
    if material['classification'] == 'human':
        require(all(any(a < hint['span'][1] and hint['span'][0] < b
                        for a, b in material['human_spans']) for hint in candidates),
                '包装中的当轮请求未进入本人片段')
        return
    disposition = material.get('current_request_disposition', {})
    decision = disposition.get('decision')
    require(decision in {'not_person', 'unresolved'} and bool(disposition.get('reason')),
            '包装含当轮请求候选，不能整段按平台模板排除；须确认本人片段或显式记录来源未定')
    require(decision != 'unresolved' or material['classification'] == 'unresolved',
            '当轮请求来源未定须保留unresolved，不能静默排除')
