"""包装中的实际请求与历史转储必须分开；结构检查不证明实名归属。"""

import unittest

from .common import ContractError
from .source_hints import current_request_candidates, validate_current_request_disposition


class SourceHintsTest(unittest.TestCase):
    def test_current_ide_request_cannot_be_discarded_with_wrapper(self):
        text = '# Context from my IDE setup:\n文件清单\n## My request:\n请修复套餐不可选逻辑。'
        with self.assertRaises(ContractError):
            validate_current_request_disposition(text, {'classification': 'system', 'human_spans': []})
        hint = current_request_candidates(text)[0]
        validate_current_request_disposition(text, {'classification': 'human', 'human_spans': [hint['span']]})
        self.assertEqual(text[slice(*hint['span'])], '请修复套餐不可选逻辑。')

    def test_historical_title_request_does_not_become_current_person(self):
        text = 'You are a helpful assistant. Generate a short title.\n历史对话：\n## My request:\n修复缺陷'
        self.assertEqual(current_request_candidates(text), [])

    def test_bridge_current_message_is_retained_as_candidate(self):
        text = ('# lark-channel-bridge 运行约定\n\n## user_message\n'
                '<bridge_context>{"senderType":"user"}</bridge_context>\n'
                '<user_input>{"text":"整理待办"}</user_input>')
        hint = current_request_candidates(text)[0]
        self.assertEqual(text[slice(*hint['span'])], '{"text":"整理待办"}')
        validate_current_request_disposition(text, {'classification': 'unresolved', 'human_spans': [],
            'current_request_disposition': {'decision': 'unresolved', 'reason': '无法确认桥接发送人与账号对应关系'}})
        with self.assertRaises(ContractError):
            validate_current_request_disposition(text, {'classification': 'auxiliary', 'human_spans': [],
                'current_request_disposition': {'decision': 'unresolved', 'reason': '来源待定'}})

    def test_bot_bridge_message_does_not_become_person(self):
        text = ('# lark-channel-bridge 运行约定\n## user_message\n'
                '<bridge_context>{"senderType":"bot"}</bridge_context>'
                '<user_input>{"text":"运行完成"}</user_input>')
        self.assertEqual(current_request_candidates(text), [])
