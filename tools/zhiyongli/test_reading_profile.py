"""成本反例检查：不把空白用户表达、生成JSON或重复执行当作可安全删除的内容。"""
import copy
import unittest

from reading_profile import experimental_events, factor_text, profile, response_shape, restore_text


class ReadingProfileTests(unittest.TestCase):
    def packet(self):
        events = []
        for i, (role, text) in enumerate([('user', ''), ('assistant', ''), ('tool', '测试失败：结果与预期不符。'),
                                         ('user', '修正后再验证'), ('tool', '测试失败：结果与预期不符。')]):
            events.append(dict(event=str(i), text=text, protocol_role=role, session_id='synthetic',
                               at='2026-11-16T10:00:00+08:00', actor_class='candidate_user' if role == 'user' else 'context',
                               field='response' if role == 'assistant' else 'request'))
        return dict(events=events, issues=[])

    def test_empty_context_experiment_keeps_empty_user_and_failure_evidence(self):
        packet = self.packet()
        selected = experimental_events(packet, 'without_empty_context')
        self.assertEqual(['0', '2', '3', '4'], [e['event'] for e in selected])
        self.assertEqual(2, sum('测试失败' in e['text'] for e in selected))

    def test_generated_json_is_not_assumed_to_be_protocol_noise(self):
        self.assertEqual('content_json', response_shape('{"title":"实际成果","description":"依据"}'))
        self.assertEqual('possible_protocol_json', response_shape('{"object":"chat.completion"}'))
        self.assertEqual('possible_sse', response_shape('data: {"delta":"文本"}\n\n'))

    def test_measurement_keeps_original_and_marks_context_removal_as_unvalidated(self):
        packet = self.packet()
        before = copy.deepcopy(packet)
        result = profile(packet)
        self.assertEqual(before, packet)
        self.assertEqual(1, result['roles']['tool']['exact_repeat_events'])
        self.assertEqual(1, result['variants']['without_empty_context']['omitted_events'])
        self.assertIn('不是已批准', result['variants']['candidate_user_only']['interpretation'])
        self.assertEqual(['0', '3'], [e['event'] for e in experimental_events(packet, 'candidate_user_only')])

    def test_short_material_does_not_claim_planned_pages_are_paid_reading(self):
        result = profile(self.packet())
        self.assertGreater(result['baseline']['pages'], 0)
        self.assertEqual(0, result['baseline']['reading_stage_pages'])

    def test_factored_body_restores_both_occurrences_and_rejects_tampering(self):
        packet = self.packet()
        packet['events'][2]['text'] = '相同失败日志，重复执行仍保留两次时点和出处。' * 100
        packet['events'][4]['text'] = packet['events'][2]['text']
        packet['events'][4]['at'] = '2026-11-16T11:00:00+08:00'
        factored = factor_text(packet)
        self.assertEqual(packet, restore_text(factored))
        self.assertEqual(1, len(factored['profile_text_bodies']))
        self.assertEqual(5, len(factored['events']))
        key = next(iter(factored['profile_text_bodies']))
        factored['profile_text_bodies'][key] = '篡改测试'
        with self.assertRaisesRegex(ValueError, '签名变化'):
            restore_text(factored)


if __name__ == '__main__':
    unittest.main()
