"""复现生产流内失败误归内容校验；只使用合成响应，不发送外部请求。"""
import io
import json
import unittest
from unittest.mock import Mock, patch

from model_config import ModelConfig
from pipeline import Paused
import test_reading_cache as fixtures


class ReadingFailureTests(unittest.TestCase):
    setUp = fixtures.ReadingCacheTests.setUp
    client = fixtures.ReadingCacheTests.client
    packet = fixtures.ReadingCacheTests.packet
    read = fixtures.ReadingCacheTests.read

    def test_gateway_terminal_failure_keeps_reason_and_never_becomes_content_error(self):
        from bounded_read import read_all
        for concurrency in (1, 2):
            with self.subTest(concurrency=concurrency):
                response = dict(status='failed', error=dict(code='gateway_concurrency_limit',
                    message='Concurrency limit exceeded for account, please retry later'))
                wire = ('data: '+json.dumps(dict(type='response.failed', response=response))+'\n\n').encode()
                opener = Mock()
                opener.open.side_effect = lambda *args, **kwargs: io.BytesIO(wire)
                client = self.client('terminal-'+str(concurrency))
                client.reading_cache_root = client.root/'shared'
                client.reading_concurrency = concurrency
                with patch.object(ModelConfig, 'opener', return_value=opener):
                    first = read_all(self.packet(1), client.root/'reading', client=client, limit=None)
                    before = {str(p): p.read_bytes() for p in client.root.rglob('*.receipt.json')}
                    second = read_all(self.packet(1), client.root/'reading', client=client, limit=None)
                self.assertEqual('reading_blocked', first['state'])
                self.assertEqual([], first['received_pages'])
                self.assertEqual('model_failed', first['blocked'][0]['state'])
                self.assertEqual('gateway_concurrency_limit', first['blocked'][0]['error_code'])
                self.assertEqual(first, second)
                self.assertEqual(1, opener.open.call_count)
                self.assertEqual(1, client.budget.used())
                self.assertEqual(before, {str(p): p.read_bytes() for p in client.root.rglob('*.receipt.json')})
                self.assertFalse(list(client.root.rglob('*.checked.json')))

    def test_blocked_reason_distinguishes_execution_content_and_unknown(self):
        from pipeline import reading_failure
        for blocked, state, text in (
            ([dict(state='model_failed', error_code='gateway_concurrency_limit')], 'needs_review', '账户或用户并发超限'),
            ([dict(state='model_failed', error_code='server_error')], 'needs_review', '模型请求执行失败'),
            ([dict(state='rejected')], 'needs_review', '模型请求执行失败'),
            ([dict(state='invalid_reading', error='引用不匹配')], 'needs_review', '未通过校验'),
            ([dict(state='unknown'), dict(state='model_failed')], 'unknown', '结果不明'),
        ):
            with self.subTest(blocked=blocked):
                failure = reading_failure(dict(blocked=blocked))
                self.assertIsInstance(failure, Paused)
                self.assertEqual(state, failure.state)
                self.assertIn(text, str(failure))


if __name__ == '__main__':
    unittest.main()
