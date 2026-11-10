"""断流诊断须可关联服务端，不能泄露片段或把部分输出当作完成。"""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from model_config import ModelConfig
from runtime import CompanyClient


class TransportDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        key=self.root/'key';key.write_text('synthetic-key');key.chmod(0o600)
        self.client=CompanyClient(ModelConfig('https://ai.apsatcom.com',key))

    def transmit(self,data,headers=None):
        stream=io.BytesIO(data);stream.headers=headers or {}
        opener=Mock();opener.open.return_value=stream
        path=self.root/'receipt.json';payload=dict(model='synthetic')
        with patch.object(ModelConfig,'opener',return_value=opener):
            record=self.client._call(payload,path)
            self.assertEqual(record,self.client._call(payload,path))
        self.assertEqual(1,opener.open.call_count)
        return record,path.read_text()

    def test_partial_stream_keeps_ids_and_diagnostics_without_delta_or_resend(self):
        data=b'data: {"type":"response.created","response":{"id":"original"}}\n\n'
        data+=b'data: {"type":"response.output_text.delta","delta":"private-fragment"}\n\n'
        record,saved=self.transmit(data,{'X-Request-ID':'gateway-1','X-Client-Request-ID':'client-1'})
        self.assertEqual('unknown',record['status'])
        self.assertEqual('stream_ended_without_terminal',record['error_code'])
        self.assertEqual('response.output_text.delta',record['last_event_type'])
        self.assertEqual('original',record['response_id'])
        self.assertEqual('gateway-1',record['gateway_request_id'])
        self.assertEqual('client-1',record['client_request_id'])
        self.assertEqual(64,len(record['last_event_sha256']))
        self.assertNotIn('private-fragment',saved)
        self.assertNotIn('response',record)

    def test_malformed_frame_has_fixed_code_without_error_body(self):
        record,saved=self.transmit(b'data: private-fragment\n\n')
        self.assertEqual('invalid_sse_json',record['error_code'])
        self.assertEqual('unknown',record['status'])
        self.assertNotIn('private-fragment',saved)

    def test_done_without_terminal_is_not_completion(self):
        record,_=self.transmit(b'data: [DONE]\n\n')
        self.assertEqual('unknown',record['status'])
        self.assertEqual('done_without_terminal',record['error_code'])

    def test_terminal_response_kept_and_untrusted_header_not_logged(self):
        response=dict(id='original',status='completed',output=[])
        frame=('data: '+json.dumps(dict(type='response.completed',response=response))+'\n\n').encode()
        record,saved=self.transmit(frame,{'X-Request-ID':'private-fragment\nunsafe'})
        self.assertEqual('received',record['status'])
        self.assertEqual(response,record['response'])
        self.assertNotIn('gateway_request_id',record)
        self.assertNotIn('private-fragment',saved)


if __name__=='__main__':unittest.main()
