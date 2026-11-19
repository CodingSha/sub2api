"""新月链仅复用新周链、片段与既有汇总；真实模型质量另行验收。"""
from types import SimpleNamespace
from datetime import timedelta
import unittest
from unittest.mock import patch

from runtime import RUBRIC,moment
from test_execution_paths import packet,record
from test_session_review import ScriptedClient
import test_session_review as fixtures
from pipeline import evaluate_month
from session_month import dispositions


class SessionMonthTests(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.SessionReviewTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        self.root=fixture.root;self.save=fixture.save

    def source(self,_source,_person,start,end,**kwargs):
        self.assertTrue(kwargs['session_review'])
        result=packet(start,end)
        if not result['events']:
            template=fixtures.sample()['events'][0]
            result['events']=[dict(template,event='test-'+start[:10],at=(moment(start)+timedelta(hours=10)).isoformat())]
        result['events'][0]['source']=dict(frame='audit:1:request:0',pointer='captured_user')
        return result

    def action(self,data):
        update=self.save(data['initial_read_id']);key=data['pending_inputs']['items'][0]['event_key']
        update['materials'][0]['event_key']=key
        for material in update['materials']:
            for loc in material['spans']:loc['event_key']=key
        for source in update['sources']:source['location']['event_key']=key
        return dict(action=dict(operation='finish',arguments=dict(update=update,
            final=dict(source_ids=['person-'+c for c in RUBRIC['items']],coverage_note='合成完整期间'))))

    def test_month_never_uses_legacy_readers_and_reuses_formal_weeks(self):
        client=ScriptedClient([self.action]);shared=self.root/'shared-session-weeks'
        args=('unused','test','2026-09-01T00:00:00+08:00','2026-10-01T00:00:00+08:00')
        with (patch('pipeline.prepare',side_effect=self.source),
                patch('pipeline.evaluate_fragment',side_effect=AssertionError('不能回旧片段')),
                patch('pipeline.month_dispositions',side_effect=AssertionError('不能回旧缺口导航'))):
            result=evaluate_month(*args,self.root/'first',client,shared_root=shared)
            self.assertEqual('81.0',result['reports'][0]['score']);self.assertEqual('L4',result['reports'][0]['rating'])
            self.assertEqual(98,client.sent)
            again=ScriptedClient([self.action]);second=evaluate_month(*args,self.root/'second',again,shared_root=shared)
            self.assertEqual('81.0',second['reports'][0]['score']);self.assertEqual(28,again.sent)
            self.assertEqual(5,len(result['reports'][0]['weekly_reports']))
            self.assertEqual(5,len(result['reports'][0]['coverage']))

    def test_month_gap_uses_existing_quotes_and_accepts_final_unknown_without_second_audit(self):
        period=dict(unit=dict(events=[dict(event='old',text='必要缺口的原文')]),
            review=dict(evidence=[dict(id='person-old',event='old',quote='必要缺口的原文',origin='direct_expression')]),
            row=dict(period_id='w1',window=dict(start='2026-09-07T00:00:00+08:00',end='2026-09-14T00:00:00+08:00'),
                cells=[dict(item='J2',state='observed',quality=dict(label='necessary_gap',refs=['person-old']))]))
        calls=[]
        class Client:
            config=SimpleNamespace(model='synthetic',responses_url='https://ai.apsatcom.com/v1/responses')
            def _call(self,payload,target):
                calls.append(payload)
                return record(dict(action=dict(operation='decide',status='unresolved',reason='月内没有决定性的恢复记录，本次不生成完整月评级。',
                    source_refs=['w1/person-old'],recovery_period=None,recovery_refs=[])),target)
        result=dispositions([period],self.root/'gap',Client())
        self.assertEqual(1,len(calls));self.assertEqual('unresolved',result[0]['status'])
        self.assertEqual([],result[0]['recovery_refs'])
