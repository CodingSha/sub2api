"""同句独立判定点可共引；同一行为换引文ID不能重复获得复查支持。"""
import copy
import unittest

from pipeline import assemble,received
from semantic_audit import shared_claim_candidates,validate
from task_identification import canonical
from test_execution_paths import FixtureClient,packet
from runtime import RUBRIC


class ClaimOwnershipTests(unittest.TestCase):
    def setUp(self):
        source=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')
        client=FixtureClient()
        self.unit=assemble(source,canonical(source,received(client.identify(source,None))))
        self.review=received(client.call(self.unit,None))
        self.audit=received(client.audit_review(self.unit,self.review,None))
        self.review['evidence'][1]['quote']=self.review['evidence'][0]['quote']
        for item in ('P1','P2'):
            self.audit['items'][item].update(own_state='observed',person_act='本人明确了同一要求。')

    def test_alias_citations_do_not_hide_same_claim(self):
        self.assertEqual([dict(items=['P1','P2'],period_ids=self.review['period_reviews'][0]['period_ids'])],shared_claim_candidates(self.review))
        result=validate(self.unit,self.review,self.audit,RUBRIC['items'])
        self.assertFalse(result['supported']);self.assertEqual(1,len(result['claim_conflicts']))
        # 保留模型原结论；程序异议单独记录，不篡改复查原响应。
        self.assertEqual([],self.audit['global_issues'])

    def test_distinct_claims_can_share_quote_without_automatic_penalty(self):
        audit=copy.deepcopy(self.audit)
        audit['items']['P2']['person_act']='本人提供了本次登录报错的具体信息。'
        self.assertTrue(validate(self.unit,self.review,audit,RUBRIC['items'])['supported'])

    def test_same_words_on_different_sources_are_not_automatically_merged(self):
        self.review['evidence'][1]['event']='other-event'
        self.assertEqual([],shared_claim_candidates(self.review))


if __name__=='__main__':unittest.main()
