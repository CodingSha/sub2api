"""完整但未合约的真实回执，只能通过有界模型纠正恢复。"""
import hashlib,json,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import jsonschema
from conversation_review import ask_conversation
from conversation_provenance import stage
from review_schema import obj
from runtime import digest

class FormatCorrectionTests(unittest.TestCase):
 def run_fixture(self,status='received',fixed=True):
  schema=obj(dict(items=obj(dict(E1=dict(type='null'))),summary=dict(type='null')))
  root=Path(self.folder);client=SimpleNamespace(config=SimpleNamespace(model='fixture',responses_url='fixture'),output_token_limit=6000);sent=[]
  def send(client,folder,name,*unused):
   sent.append(name);value=dict(items=dict(E1=None))
   if fixed and '-formatfix-' in name:value['summary']=None
   payload=json.loads((folder/(name+'.intent.json')).read_text())['payload'];(folder/(name+'.input.json')).write_text(json.dumps(payload))
   receipt=dict(status=status,model='fixture',endpoint='fixture',request_sha256=digest(dict(endpoint='fixture',payload=payload)),wire_body_sha256=hashlib.sha256(json.dumps(payload,ensure_ascii=False).encode()).hexdigest(),response=dict(status='completed',output=[dict(content=[dict(type='output_text',text=json.dumps(value))])]))
   (folder/(name+'.receipt.json')).write_text(json.dumps(receipt))
   # 真正发送器在收到完整JSON时落盘；格式校验由上层完成。
   (folder/(name+'.json')).write_text(json.dumps(value));return value
  try:value=ask_conversation(client,root,'repair-fixture-1',dict(records=[]),schema,'修正',send,300)
  except jsonschema.ValidationError:return sent,None
  return sent,value
 def test_completed_missing_field_is_model_corrected_and_traceable(self):
  with TemporaryDirectory() as self.folder:
   sent,value=self.run_fixture();self.assertEqual(len(sent),2);self.assertEqual(value,dict(items=dict(E1=None),summary=None))
   self.assertTrue(sent[-1].endswith('-1'));self.assertEqual(stage(Path(self.folder),sent[-1])[0],value)
   proof=Path(self.folder)/(sent[-1]+'.format-correction.json');v=json.loads(proof.read_text());v['parent_receipt_sha256']='tampered';proof.write_text(json.dumps(v))
   with self.assertRaises(ValueError):stage(Path(self.folder),sent[-1])
 def test_unknown_is_never_retried(self):
  with TemporaryDirectory() as self.folder:
   sent,value=self.run_fixture(status='unknown');self.assertEqual(len(sent),1);self.assertIsNone(value)
 def test_still_invalid_stops_after_two_corrections(self):
  with TemporaryDirectory() as self.folder:
   sent,value=self.run_fixture(fixed=False);self.assertEqual(len(sent),3);self.assertIsNone(value)

if __name__=='__main__':unittest.main()
