"""空期间、真实恢复出处与跨期间串联；不把合成行为当作员工分数。"""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from month_evidence import freeze_empty,audited_empty,validate
from pipeline import evaluate_month,Paused


def packet(start='2026-09-07T00:00:00+08:00',end='2026-09-14T00:00:00+08:00'):
    return dict(user_id='test',user_email='test@example.test',since=start,until=end,events=[],issues=[],source_snapshot={'sha256':'a'*64})

class MonthTests(unittest.TestCase):
    def test_empty_requires_actual_saved_audit_and_no_context_or_source_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            record=freeze_empty(packet(),tmp)
            u,v,row=audited_empty(record)
            self.assertEqual([],u['source_keys']);self.assertTrue(all(c['state']=='no_opportunity' for c in row['cells']))
            for field,value in [('events',[dict(text='只有AI回复也不能直接宣布没有输入')]),('issues',['来源不完整'])]:
                bad=packet();bad[field]=value
                with self.assertRaises(ValueError):freeze_empty(bad,tmp)
            Path(record['audit_ref']['path']).write_text('{}')
            with self.assertRaises(ValueError):audited_empty(record)

    def test_all_empty_month_visits_every_week_and_never_calls_model(self):
        client=Mock();ranges=[]
        def source(_root,_user,start,end):ranges.append((start,end));return packet(start,end)
        with tempfile.TemporaryDirectory() as tmp,patch('pipeline.prepare',side_effect=source):
            with self.assertRaises(Paused) as caught:
                evaluate_month('unused','test','2026-09-01T00:00:00+08:00','2026-10-01T00:00:00+08:00',tmp,client)
            self.assertEqual('no_activity',caught.exception.state)
            self.assertEqual(5,len([p for p in Path(tmp).glob('weeks/*/empty.json')]))
            self.assertEqual(2,len(list(Path(tmp).glob('fragments/*/empty.json'))))
            self.assertEqual([],client.mock_calls)

    def test_recovery_requires_later_period_and_matching_actual_quote(self):
        first=dict(unit={'events':[dict(event='old',text='必要缺口的原文')]},review={'evidence':[]},row=dict(period_id='w1',
            window=dict(start='2026-09-07T00:00:00+08:00',end='2026-09-14T00:00:00+08:00'),
            cells=[dict(item='J2',state='observed',quality=dict(label='necessary_gap'))]))
        later=dict(unit={'events':[dict(event='new',text='已实际完成核验及处理缺口')]},review={'evidence':[dict(id='proof',event='new',quote='实际完成核验')]},
            row=dict(period_id='w2',window=dict(start='2026-09-14T00:00:00+08:00',end='2026-09-21T00:00:00+08:00'),cells=[]))
        d=dict(status='recovered',reason='后续原文支持',source_quotes=[dict(period_id='w2',event='new',quote='实际完成核验')],recovery_period='w2',recovery_refs=['proof'])
        self.assertEqual('recovered',validate([first,later],dict(slots={'w1/J2':d}))[0]['status'])
        for changes in (dict(recovery_period='w1'),dict(recovery_refs=['missing']),dict(source_quotes=[dict(period_id='w2',event='new',quote='声称已恢复但原文没有')]),dict(status='unresolved')):
            with self.assertRaises(ValueError):validate([first,later],dict(slots={'w1/J2':dict(d,**changes)}))

if __name__=='__main__':unittest.main()
