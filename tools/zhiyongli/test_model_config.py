"""内网入口严格限域；禁止代理转发及重定向，不放宽密钥文件保护。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

from model_config import MODEL, ModelConfig, NoRedirect


class ModelConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.key = self.root/'key'
        self.key.write_text('synthetic-key')
        self.key.chmod(0o600)
        self.path = self.root/'model.json'

    def load(self, base, **fields):
        self.path.write_text(json.dumps(dict(base_url=base, model=MODEL,
            api_key_file=str(self.key), **fields)))
        return ModelConfig.load(self.path)

    def test_explicit_company_and_container_endpoints(self):
        for origin in ('https://ai.apsatcom.com', 'http://sub2api:8080'):
            for suffix in ('', '/', '/v1', '/v1/'):
                with self.subTest(origin=origin, suffix=suffix):
                    config = self.load(origin+suffix)
                    self.assertEqual(origin+'/v1/responses', config.responses_url)
                    self.assertEqual('synthetic-key', config.read_key())

    def test_other_origins_credentials_paths_and_url_options_rejected(self):
        for base in (
            'http://ai.apsatcom.com', 'https://other.example', 'http://sub2api',
            'http://sub2api:8180', 'http://127.0.0.1:8180', 'http://sub2api:8080.evil',
            'http://sub2api:8080@other.example', 'http://user@sub2api:8080',
            'https://ai.apsatcom.com:443', 'http://sub2api:8080/v1/responses',
            'http://sub2api:8080?key=value', 'http://sub2api:8080/#fragment',
            '//sub2api:8080', 'http://sub2api:8080/%2e%2e',
            'http://sub2api:8080?', 'http://sub2api:8080#',
            'http://sub2api:8080\n', 'http://sub2api:\t8080', None,
        ):
            with self.subTest(base=base), self.assertRaises(ValueError):
                self.load(base)

    def test_company_entries_do_not_use_system_proxy_or_redirects(self):
        for origin in ('https://ai.apsatcom.com', 'http://sub2api:8080'):
            with self.subTest(origin=origin), patch('urllib.request.getproxies',
                    return_value={'http': 'http://proxy.example:3128', 'https': 'http://proxy.example:3128'}):
                opener = self.load(origin).opener()
                self.assertFalse(any(isinstance(h, urllib.request.ProxyHandler) for h in opener.handlers))
                redirects = [h for h in opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)]
                self.assertEqual(1, len(redirects))
                self.assertIsInstance(redirects[0], NoRedirect)
                self.assertIsNone(redirects[0].redirect_request(None, None, 307, '', {}, 'http://other.example'))

    def test_key_permissions_and_model_restrictions_still_apply(self):
        config = self.load('http://sub2api:8080')
        self.key.chmod(0o644)
        with self.assertRaises(ValueError):
            config.read_key()
        self.key.chmod(0o400)
        self.assertEqual('synthetic-key', config.read_key())
        for field in ({'timeout_seconds': True}, {'timeout_seconds': 1801}, {'extra': 'field'}):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.load('http://sub2api:8080', **field)
        value = json.loads(self.path.read_text())
        value.pop('extra', None)
        value['model'] = 'other-model'
        self.path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            ModelConfig.load(self.path)


if __name__ == '__main__':
    unittest.main()
