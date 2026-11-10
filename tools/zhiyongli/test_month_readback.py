"""月末长来源只按实际提供原文核对；合成恢复不代表真实员工判断。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from month_readback import complete
from context_navigation import Navigation
from runtime import atomic
from pipeline import received,Paused
from test_execution_paths import record
import test_context_navigation as navtests


def setup(root):
    periods=[];directories={}
    for pid,start,end,eid,text in [('w1','2026-09-07','2026-09-14','old','合成必要缺口。'),
                                  ('w2','2026-09-14','2026-09-21','new','已实际完成核验及处理缺口。')]:
        directory=root/pid;directories[pid]=directory
        packet=dict(user_id='test',user_email='test@example.test',since=start+'T00:00:00+08:00',until=end+'T00:00:00+08:00',
            events=[dict(event='context-'+str(i),text='合成背景'+str(i)+'x'*1000,actor_class='context',at=start+'T10:00:00+08:00')
                for i in range(100)]+[dict(event=eid,text=text,actor_class='context',at=start+'T12:00:00+08:00')])
        atomic(directory/'packet.json',packet)
        nav=Navigation(directory/'context-navigation',packet);nav.build(navtests.NavigationTests().notes(packet),navtests.Reader(),received)
        periods.append(dict(unit=dict(user_id='test',periods=[dict(start=packet['since'],end=packet['until'])],events=packet['events']),
            review=dict(evidence=[dict(id='proof-'+pid,event=eid,quote=text)]),row=dict(period_id=pid,
                window=dict(start=packet['since'],end=packet['until']),cells=[dict(item='J2',state='observed',
                    quality=dict(label='necessary_gap' if pid=='w1' else 'sufficient',refs=['proof-w1'] if pid=='w1' else []))])))
    return periods,directories


class Client:
    def __init__(self):self.calls=0;self.inputs=[];self.force_unread=False;self.disagree=False
    def month_status_view(self,data,schema,path):
        if path.exists():return json.loads(path.read_text())
        self.calls+=1;self.inputs.append(data)
        view=next(v for v in data['periods'] if v['period_id']=='w2');access=view['context_access']
        if not access['raw_read_fragments'] and not self.force_unread:
            observed={o['node_id']:o['node'] for o in access['observations'] if o['mode']=='inspect'}
            key=access['root']['node_id']
            while key in observed and observed[key]['kind']=='branch':key=observed[key]['children'][-1]
            mode='raw' if key in observed and observed[key]['kind']=='leaf' else 'inspect'
            return record(dict(slots={},needs_period_nodes=[dict(period_id='w2',node_id=key,mode=mode)]),path)
        recovered=not (self.disagree and data['previous'] is not None)
        proof=next(e for e in view['evidence_index'] if e['id']=='proof-w2')
        decision=dict(status='recovered' if recovered else 'open',reason='仅合成缺口恢复合同',
            source_quotes=[dict(period_id='w2',event=proof['event'],quote='已实际完成核验及处理缺口。')],
            recovery_period='w2' if recovered else None,recovery_refs=['proof-w2'] if recovered else [])
        return record(dict(slots={'w1/J2':decision},needs_period_nodes=[]),path)


class MonthReadbackTests(unittest.TestCase):
    def test_raw_original_can_expose_previously_approved_human_evidence_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);periods,dirs=setup(root);later=periods[1]
            event=copy.deepcopy(later['unit']['events'][-1]);event.update(event='new-human-0',source_event='new',
                source_span=[0,len(event['text'])],actor_class='human',human_spans=[[0,len(event['text'])]])
            later['unit']['events'].append(event);later['review']['evidence'][0]['event']='new-human-0'
            result=complete(periods,dirs,'2026-09-01T00:00:00+08:00','2026-10-01T00:00:00+08:00',root/'review',Client(),received)
            self.assertEqual('recovered',result[2][0]['status'])

    def test_candidate_and_auditor_independently_read_later_source_and_bind_native_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);periods,dirs=setup(root);before=copy.deepcopy(periods);client=Client()
            result=complete(periods,dirs,'2026-09-01T00:00:00+08:00','2026-10-01T00:00:00+08:00',root/'review',client,received)
            self.assertEqual('recovered',result[2][0]['status']);self.assertEqual(result[2],result[3])
            self.assertEqual(before,periods)
            from bounded_read import raw_size
            self.assertTrue(all(raw_size(data)<120000 for data in client.inputs))
            self.assertTrue(any(d['previous'] is None for d in client.inputs));self.assertTrue(any(d['previous'] for d in client.inputs))
            calls=client.calls;self.assertEqual(result,complete(periods,dirs,'2026-09-01T00:00:00+08:00','2026-10-01T00:00:00+08:00',root/'review',client,received))
            self.assertEqual(calls,client.calls)

    def test_raw_on_disk_is_not_evidence_until_provided_and_prior_candidate_is_not_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);periods,dirs=setup(root);client=Client();client.force_unread=True
            with self.assertRaisesRegex(ValueError,'未实际提供'):complete(periods,dirs,'2026-09-01T00:00:00+08:00','2026-10-01T00:00:00+08:00',root/'review',client,received)
            self.assertEqual(1,client.calls)

    def test_unknown_is_not_resent_and_source_change_cannot_reuse_prior_chain(self):
        class Unknown(Client):
            def month_status_view(self,data,schema,path):
                if path.exists():return json.loads(path.read_text())
                self.calls+=1;value=dict(status='unknown');atomic(path,value);return value
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);periods,dirs=setup(root);client=Unknown()
            for _ in range(2):
                with self.assertRaises(Paused) as caught:complete(periods,dirs,'2026-09-01T00:00:00+08:00','2026-10-01T00:00:00+08:00',root/'review',client,received)
                self.assertEqual('unknown',caught.exception.state)
            self.assertEqual(1,client.calls)
            periods[0]['unit']['events'][0]['text']='改变来源'
            with self.assertRaises(ValueError):complete(periods,dirs,'2026-09-01T00:00:00+08:00','2026-10-01T00:00:00+08:00',root/'review',client,received)
            self.assertEqual(1,client.calls)

    def test_second_check_disagreement_remains_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);periods,dirs=setup(root);client=Client();client.disagree=True
            result=complete(periods,dirs,'2026-09-01T00:00:00+08:00','2026-10-01T00:00:00+08:00',root/'review',client,received)
            self.assertNotEqual(result[2][0]['status'],result[3][0]['status'])


if __name__=='__main__':unittest.main()
