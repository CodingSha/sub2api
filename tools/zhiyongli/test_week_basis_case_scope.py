"""案例六项检查只引用当前案例；全合成，不证明语义质量。"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

import week_basis as stage
import test_week_basis as fixture
from pipeline import received


def sample(stage):
    # 复用同目录已有纯合成夹具，不读取任何员工记录或生产结果。
    case=fixture.Cases();case.setUp()
    try:
        data=stage.prepare(case.unit,case.bundle['reports'][0],case.parent)
        client=fixture.Client()
        original=received(client._call(stage.request(client,'zhiyongli_week_basis',stage.GENERATE,data,stage.schema(data)),case.root/'generation.receipt.json'))
        foreign=copy.deepcopy(data['records'][0]);foreign['id']='other-task-record'
        data['records'].append(foreign)
        data['source_units'].append(dict(key='other-task',record_ids=[foreign['id']]))
        original['covered_record_ids'].append(foreign['id'])
        audit=received(client._call(stage.request(client,'zhiyongli_week_basis_audit',stage.AUDIT,
            dict(source=data,interpretation=original),stage.audit_schema(data,original)),case.root/'audit.receipt.json'))
        return data,original,audit
    finally:case.tearDown()


class CaseScopeTests(unittest.TestCase):
    stage=stage

    def setUp(self):self.data,self.spec,self.audit=sample(self.stage)

    def test_optimized_python_keeps_formal_source_gate_without_sending(self):
        # 单独优化解释器真实运行，防止assert在-O下消失；不使用当前进程模块缓存。
        code = """
import json
from runtime import atomic
import test_week_basis as fixtures
case=fixtures.Cases();case.setUp()
try:
    case.bundle['reports'][0]['eligibility']['observations']=[dict(task_keys=['other-formal-task'])]
    atomic(case.parent,case.bundle)
    client=fixtures.Client()
    try:case.run_stage(client)
    except ValueError as error:
        if '正式任务资格与冻结来源集合不一致' not in str(error):raise
    else:raise RuntimeError('正式任务不一致被放行')
    if __debug__ or client.calls or (case.root/'new').exists():raise RuntimeError('优化模式门禁未在发送前阻断')
    print(json.dumps(dict(optimized=True,model_calls=client.calls)))
finally:case.tearDown()
"""
        result=subprocess.run([sys.executable,'-O','-c',code],cwd=Path(__file__).resolve().parent,
            capture_output=True,text=True,timeout=20)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertEqual(dict(optimized=True,model_calls=0),json.loads(result.stdout))

    def test_valid_current_case_checks_are_accepted(self):
        self.stage.validate_audit(self.data,self.spec,self.audit)

    def test_each_case_check_rejects_other_task_record(self):
        for key in self.stage.role.CHECKS:
            with self.subTest(check=key):
                audit=copy.deepcopy(self.audit);audit['case_checks'][key]['record_ids']=['other-task-record']
                with self.assertRaises(ValueError):self.stage.validate_audit(self.data,self.spec,audit)

    def test_case_claims_keep_existing_scope_constraint(self):
        audit=copy.deepcopy(self.audit)
        audit['case_claims']['claim_0']['record_ids']=['other-task-record']
        with self.assertRaises(ValueError):self.stage.validate_audit(self.data,self.spec,audit)

    def test_schema_limits_claims_and_checks_to_case_records(self):
        schema=self.stage.audit_schema(self.data,self.spec)
        for group in ('case_claims','case_checks'):
            for key,row in schema['properties'][group]['properties'].items():
                with self.subTest(group=group,key=key):
                    self.assertEqual(set(self.data['case_record_ids']),set(row['properties']['record_ids']['items']['enum']))
        self.assertIn('other-task-record',schema['properties']['week']['properties']['P1']['properties']['record_ids']['items']['enum'])

    def test_week_items_can_still_use_other_task_evidence(self):
        spec=copy.deepcopy(self.spec);audit=copy.deepcopy(self.audit)
        spec['items']['P1']['source_keys']=['other-task'];spec['items']['P1']['record_ids']=['other-task-record']
        audit['week']['P1']['record_ids']=['other-task-record']
        self.stage.validate_audit(self.data,spec,audit)

    def test_rejection_does_not_rewrite_inputs(self):
        audit=copy.deepcopy(self.audit);audit['case_checks']['outcome']['record_ids']=['other-task-record']
        before=copy.deepcopy((self.data,self.spec,audit))
        with self.assertRaises(ValueError):self.stage.validate_audit(self.data,self.spec,audit)
        self.assertEqual(before,(self.data,self.spec,audit))


if __name__=='__main__':unittest.main()
