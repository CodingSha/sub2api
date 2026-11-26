import unittest
from evidence_requests import read_package,search,validate_requests
from test_task_card import task


class EvidenceRequestsTests(unittest.TestCase):
    def setUp(self):
        self.task=task();self.originals={r['id']:r for r in self.task['records']}
        self.originals['tool']=dict(id='tool',role='tool',text='前文'*5000+'target-hit'+'后文'*5000)
    def test_foreign_negative_and_oversized_coordinates_are_rejected(self):
        for request in [dict(record_id='foreign',start=0,chars=2),dict(record_id='u1',start=-1,chars=2),
                        dict(record_id='u1',start=0,chars=5000)]:
            with self.assertRaises(ValueError):validate_requests([request],self.originals)
    def test_search_locates_middle_and_read_is_actual_slice(self):
        hit=search(self.originals,['target-hit'])[0]['hits'][0]
        rows=read_package(self.task,self.originals,[{k:hit[k] for k in ('record_id','start','chars')}])
        self.assertIn('target-hit',rows[0]['text']);self.assertTrue(rows[0]['excerpt'])
        self.assertEqual(rows[0]['text'],self.originals['tool']['text'][rows[0]['start']:rows[0]['end']])
    def test_read_preserves_neighbor_context_and_marks_partial_source(self):
        rows=read_package(self.task,self.originals,[dict(record_id='u1',start=0,chars=4)])
        self.assertTrue(any(r['id']=='a1' for r in rows))
        self.assertTrue(any(r['id']=='u1' and r['excerpt'] for r in rows))

    def test_directory_never_silently_drops_source_records(self):
        from evidence_requests import directory,MAX_DIRECTORY_BYTES
        import json
        rows={str(i):dict(id=str(i),role='tool',text='长预览'*100) for i in range(100)}
        result=directory(rows)
        self.assertEqual(len(result),len(rows))
        self.assertLessEqual(len(json.dumps(result,ensure_ascii=False).encode()),MAX_DIRECTORY_BYTES)
        self.assertTrue(all(not row['preview_is_evidence'] for row in result))
        with self.assertRaisesRegex(ValueError,'分段导航'):
            directory({str(i):dict(id=str(i),role='tool',text='内容') for i in range(1000)})
