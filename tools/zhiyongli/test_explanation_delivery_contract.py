"""正式展示UTF-8字节合同；纯合成回执，不发网络。"""
import unittest
from unittest import mock
import test_week_basis as fixture
import week_basis as stage


class DeliveryContract(unittest.TestCase):
    def setUp(self):
        self.f = fixture.Cases(); self.f.setUp(); self.addCleanup(self.f.tearDown)

    def test_generation_delivery_overflow_stops_before_paid_audit(self):
        for field in ('outcome', 'claim', 'assessment', 'week_reason'):
            with self.subTest(field=field):
                def change(kind, value):
                    if kind != 'zhiyongli_week_basis': return value
                    if field == 'outcome': value['case']['outcome'] = '字'*2001
                    if field == 'claim': value['case']['claims'][0]['text'] = '字'*2001
                    if field == 'assessment': value['case']['claims'][0]['assessment']['reason'] = '字'*801
                    if field == 'week_reason':
                        value['items']['P1']['basis'] = '字'*1000
                        value['items']['P1']['higher_band'] = '字'*1000
                    return value
                client = fixture.Client(change)
                root = self.f.root/field
                with self.assertRaises(ValueError):
                    stage.enrich(self.f.bundle,self.f.unit,self.f.parent,root,client)
                self.assertEqual(client.calls,1)
                self.assertTrue((root/'generation.receipt.json').exists())
                self.assertFalse((root/'review.receipt.json').exists())
                before = (root/'generation.receipt.json').read_bytes()
                with self.assertRaises(ValueError):
                    stage.enrich(self.f.bundle,self.f.unit,self.f.parent,root,client)
                self.assertEqual(client.calls,1)
                self.assertEqual(before,(root/'generation.receipt.json').read_bytes())

    def test_title_and_goal_overflow_stop_before_generation(self):
        for key, length in [('title',201),('goal',2001)]:
            with self.subTest(key=key):
                data=stage.prepare(self.f.unit,self.f.bundle['reports'][0],self.f.parent)
                data['case'][key]='字'*length
                client=fixture.Client()
                with mock.patch.object(stage,'prepare',return_value=data):
                    with self.assertRaises(ValueError):
                        stage.enrich(self.f.bundle,self.f.unit,self.f.parent,self.f.root/key,client)
                self.assertEqual(client.calls,0)

    def test_composed_week_reason_uses_exact_delivery_separator(self):
        data=stage.prepare(self.f.unit,self.f.bundle['reports'][0],self.f.parent)
        captured=[]
        def capture(kind,value):
            if kind=='zhiyongli_week_basis': captured.append(value)
            return value
        stage.enrich(self.f.bundle,self.f.unit,self.f.parent,self.f.root/'base',fixture.Client(capture))
        spec=captured[0]
        spec['items']['P1']['basis']='字'*1000
        spec['items']['P1']['higher_band']='x'*2984
        stage.validate_spec(data,spec)
        spec['items']['P1']['higher_band']+='x'
        with self.assertRaises(ValueError): stage.validate_spec(data,spec)

    def test_exact_utf8_boundaries_do_not_reject_by_character_count(self):
        stage.role.delivery_text('字'*2000,6000,'结果')
        stage.role.delivery_text('字'*800,2400,'作用')
        stage.role.delivery_text('x'*6000,6000,'结果')
        stage.role.delivery_text('字'*1999+'abc',6000,'结果')
        with self.assertRaises(ValueError): stage.role.delivery_text('字'*2000+'x',6000,'结果')
        with self.assertRaises(ValueError): stage.role.delivery_text(' ',6000,'结果')

if __name__=='__main__': unittest.main()
