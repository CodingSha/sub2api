"""阻止格式定义错误消耗真实模型请求，并保留格式合法但无正文的失败边界。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from daily_pilot import call
from review_schema import check_local_schema, compact, obj


class DailySchemaTests(unittest.TestCase):
    def test_nested_compact_root_reference_is_rejected_before_send(self):
        repeated=obj(dict(reason=dict(type='string',maxLength=200),refs=dict(type='array',items=dict(type='string',maxLength=80))))
        inner=compact(obj(dict(first=repeated,second=repeated)))
        self.assertIn('$defs',inner)
        schema=obj(dict(items=inner))
        client=Mock()
        with tempfile.TemporaryDirectory() as path:
            with self.assertRaisesRegex(ValueError,'引用不存在'):
                call(client,Path(path),'score',{},schema,'只返回测试结构')
            client._call.assert_not_called()
            self.assertFalse(list(Path(path).iterdir()))
        definitions=inner.pop('$defs')
        schema['$defs']=definitions
        check_local_schema(schema)

    def test_external_schema_reference_is_not_resolved_over_network(self):
        with self.assertRaisesRegex(ValueError,'仅允许本地根引用'):
            check_local_schema(obj(dict(x={'$ref':'https://example.test/schema'})))


if __name__=='__main__':unittest.main()
