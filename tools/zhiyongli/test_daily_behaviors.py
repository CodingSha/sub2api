"""验证共引与重复行为的区别，不能以此代替模型的语义质量验收。"""
import unittest
from daily_behaviors import catalog, validate, check_evidence, original_quote, source_fragments


class DailyBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.text='修复登录失败，并用原失败步骤回归。'
        self.delivered={'u':dict(role='user',text=self.text),'ai':dict(role='assistant',text=self.text)}
        self.action=dict(record_id='u',quote=self.text,primary_item='P1',claim='要求修复登录失败')

    def test_independent_goal_and_check_use_distinct_parts_of_the_same_message(self):
        result=catalog([dict(self.action,quote='修复登录失败'),dict(self.action,quote='用原失败步骤回归',primary_item='J2',claim='指定原失败步骤作为回归方法')],self.delivered)
        self.assertEqual(len(result),2)
        for identity,behavior in result.items():
            check_evidence(result,behavior['primary_item'],identity,'u',self.text)
        identity=next(iter(result))
        with self.assertRaisesRegex(ValueError,'不能跨项重复'):
            check_evidence(result,'J2',identity,'u',self.text)

    def test_same_asset_selection_cannot_be_renamed_into_two_scoring_items(self):
        text='参考原有的端站入网脚本。'
        first=dict(record_id='u',quote=text,primary_item='E3',claim='复用入网脚本')
        second=dict(first,quote='端站入网脚本',primary_item='P2',claim='提供入网脚本信息')
        with self.assertRaisesRegex(ValueError,'跨项重叠'):
            catalog([first,second],{'u':dict(role='user',text=text)})

    def test_quote_must_locate_the_actual_source_unambiguously(self):
        for quote in ('不是原文','修复'):
            with self.assertRaisesRegex(ValueError,'唯一定位'):
                catalog([dict(self.action,quote=quote)],{'u':dict(role='user',text='修复登录，修复错误')})

    def test_whitespace_recovery_returns_the_real_original_without_rewriting_words(self):
        self.assertEqual(original_quote('用例\u00a0 17720','用例 17720'),'用例\u00a0 17720')
        with self.assertRaises(ValueError):original_quote('用例\u00a0 17720','用例 17722')

    def test_fragment_ids_preserve_paths_and_bound_index_size_without_losing_full_message(self):
        text='E:\\航运\\xthx.tar，转为.zip；保留原件。'+('继续当前安排，'*30)
        records={'u':dict(role='user',text=text),'a':dict(role='assistant',text='工具已完成')}
        fragments=source_fragments(records)
        self.assertLessEqual(len(fragments),4)
        self.assertEqual(''.join(f['quote'] for f in fragments.values()),text)
        self.assertEqual(records['u']['text'],text)
        self.assertTrue(all(f['record_id']=='u' and f['quote'] in text for f in fragments.values()))

    def test_renaming_the_scoring_item_does_not_create_a_second_behavior(self):
        with self.assertRaisesRegex(ValueError,'重复分配'):
            catalog([self.action,dict(self.action,primary_item='M1')],self.delivered)
        one=catalog([self.action],self.delivered)
        identity=next(iter(one))
        renamed={**one,'another':dict(one[identity],primary_item='M1')}
        with self.assertRaisesRegex(ValueError,'唯一主归'):
            validate(renamed,self.delivered)

    def test_ai_or_undelivered_text_cannot_be_claimed_as_personal_behavior(self):
        for bad in (dict(self.action,record_id='ai'),dict(self.action,claim=' ')):
            with self.assertRaises(ValueError):catalog([bad],self.delivered)


if __name__=='__main__':unittest.main()
