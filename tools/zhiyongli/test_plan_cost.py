"""分页容量优化必须逐字保留原页边界与实际请求字节合同。"""
import copy
from datetime import timedelta, timezone
import unittest
from unittest.mock import patch

import bounded_read as reading
from runtime import moment
import test_reading_cache as fixtures


def original_pages(packet, maximum):
    result = []; current = []; previous = None
    for fragment in reading.iter_fragments(packet, maximum):
        day = moment(fragment['at']).astimezone(timezone(timedelta(hours=8))).date() if fragment.get('at') else None
        if current and (day != previous or len(current) >= reading.PAGE_FRAGMENTS
            or reading.raw_size(dict(fragments=current+[fragment])) > maximum
            or reading.request_size(dict(fragments=current+[fragment])) > reading.REQUEST_BYTES):
            result.append(dict(fragments=current)); current = []
        if reading.raw_size(dict(fragments=[fragment])) > maximum:
            raise ValueError('片段包装超过阅读预算')
        if reading.request_size(dict(fragments=[fragment])) > reading.REQUEST_BYTES:
            raise ValueError('单个完整阅读片段的实际请求结构超容量，发送前暂停，不裁掉原文')
        current.append(fragment); previous = day
    if current:
        result.append(dict(fragments=current))
    return result


class PlanCostTests(unittest.TestCase):
    packet = fixtures.ReadingCacheTests.packet

    def test_page_boundaries_exactly_match_original_serializer(self):
        for maximum in (4096, 9000, reading.PAGE_BYTES):
            packet = self.packet(53)
            for i, event in enumerate(packet['events']):
                event['text'] = ('中文🙂\n\t"\\'*(i*43)) if i % 7 else ''
                event['at'] = '2026-09-'+('07' if i < 27 else '08')+'T12:00:00+08:00'
                event['event'] += '"\\\n' if i % 3 else '🙂'
            original = copy.deepcopy(packet)
            with self.subTest(maximum=maximum):
                expected = original_pages(packet, maximum)
                result = reading.plan(packet, maximum)
                self.assertEqual(expected, result['pages'])
                for page in result['pages']:
                    self.assertLessEqual(reading.raw_size(page), maximum)
                    self.assertLessEqual(reading.request_size(page), reading.REQUEST_BYTES)
                indexed = reading.plan(packet, maximum, include_text=False)
                events = {e['event']: e for e in packet['events']}
                self.assertEqual(expected, [reading.source_page(p, events) for p in indexed['pages']])
                self.assertEqual(original, packet)

    def test_request_capacity_still_splits_pages_at_exact_original_boundary(self):
        packet = self.packet(30)
        for e in packet['events']:
            e['text'] = '含转义"\\🙂'*150
        for cap in (12000, 24000):
            with self.subTest(cap=cap), patch.object(reading, 'REQUEST_BYTES', cap):
                self.assertEqual(original_pages(packet, reading.PAGE_BYTES), reading.plan(packet)['pages'])

    def test_each_fragment_is_serialized_once_for_capacity_not_repeated_page_copies(self):
        packet = self.packet(100)
        count = len(list(reading.iter_fragments(packet)))
        with patch.object(reading, 'request_size', wraps=reading.request_size) as sizes:
            reading.plan(packet)
        self.assertLessEqual(sizes.call_count, count+1)


if __name__ == '__main__':
    unittest.main()
