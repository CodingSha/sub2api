"""实际已核链到正式说明增强包；保持原分并拒绝说明异议。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pipeline import evaluate_week, Paused
from shared_weeks import accepted_reference, read_ref, reference
from runtime import atomic
from test_execution_paths import FixtureClient, packet
from test_explanation_stage import BasisClient
from week_presentation import complete, build
from workday_scope import window


class WeekPresentationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.shared = self.root/'shared'
        self.directory = self.root/'scoring'; self.output = self.root/'explanation'
        self.source = packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')
        self.source['workday_scope'] = window('2026-09-07','2026-09-14')
        self.bundle = evaluate_week(self.source, self.directory, FixtureClient(), shared_root=self.shared)
        self.entry = accepted_reference(self.shared, self.source, self.directory)

    def test_accepted_score_gets_complete_formal_review_and_resume_sends_zero(self):
        client = BasisClient(); result = complete(self.entry, self.output, client)
        self.assertEqual(2, client.calls)
        review = result['reports'][0].pop('task_review')
        self.assertEqual(self.bundle, result)
        self.assertEqual(('task-evidence-20261106.1','week_checked',12),
            (review['version'], review['status'], len(review['week_judgments'])))
        self.assertEqual(set(review['task_keys']), set(review['checked_task_keys']))
        self.assertEqual(review['cases'][0]['record_count'], len(review['cases'][0]['records']))
        original = json.loads((self.directory/'bundle.json').read_text())
        self.assertEqual(self.bundle, original)
        first = json.loads((self.output/'presentation-bundle.json').read_text())
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.output.rglob('*') if p.is_file()}
        self.assertEqual(first, complete(self.entry, self.output, client))
        self.assertEqual(2, client.calls)
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before})

    def test_shared_pointer_uses_original_accepted_index_and_same_explanation(self):
        other = self.root/'other-job'
        evaluate_week(self.source, other, FixtureClient(), shared_root=self.shared)
        self.assertEqual(self.entry, accepted_reference(self.shared,self.source,other))
        client = BasisClient()
        result = complete(self.entry,self.output,client)
        self.assertEqual(result, complete(accepted_reference(self.shared,self.source,other),self.output,client))
        self.assertEqual(2,client.calls)

    def test_formal_projection_does_not_invent_task_links_or_erase_reassessment(self):
        complete(self.entry,self.output,BasisClient())
        data = json.loads((self.output/'basis/input.json').read_text())['data']
        spec = json.loads((self.output/'basis/spec.json').read_text())
        for scenario in ('reassessment','local_only','wrong_case','missing_record','existing_review'):
            with self.subTest(scenario=scenario):
                candidate = copy.deepcopy(self.bundle); source = copy.deepcopy(data); value = copy.deepcopy(spec)
                if scenario == 'reassessment':value['items']['P1']['verdict'] = 'needs_reassessment'
                elif scenario == 'local_only':value['items']['P1']['source_keys'] = ['local-only']
                elif scenario == 'wrong_case':value['case']['task_key'] = 'foreign'
                elif scenario == 'missing_record':source['records'] = []
                else:candidate['reports'][0]['task_review'] = {}
                with self.assertRaises((ValueError,Paused)):build(candidate,source,value)
                self.assertEqual(self.bundle, read_ref(read_ref(self.entry)['files']['bundle.json']))

    def test_canonical_source_and_mode_cannot_be_replaced_by_another_index(self):
        other = copy.deepcopy(self.source); other['user_email'] = 'other@example.invalid'
        with self.assertRaises(ValueError):accepted_reference(self.shared,other,self.directory)
        with self.assertRaises(ValueError):accepted_reference(self.shared,self.source,self.directory,page_navigation=True)

    def test_presentation_damage_is_not_overwritten_or_used_for_import(self):
        client = BasisClient(); complete(self.entry,self.output,client)
        target = self.output/'presentation-bundle.json'; target.write_text('{"tampered":true}')
        before = target.read_bytes()
        with self.assertRaises(ValueError):complete(self.entry,self.output,client)
        self.assertEqual((2,before),(client.calls,target.read_bytes()))

    def test_reregistered_spec_aba_cannot_import_unverified_snapshot(self):
        import week_presentation
        original_enrich=week_presentation.enrich;original_build=week_presentation.build
        saved={};client=BasisClient()
        def substitute(*args,**kwargs):
            result=original_enrich(*args,**kwargs)
            root=Path(args[1])
            for target in (root/'basis/spec.json',root/'basis/manifest.json',root/'result.json'):
                saved[target]=target.read_bytes()
            spec=json.loads((root/'basis/spec.json').read_text())
            spec['items']['P1']['basis']='未经复核的替换理由'
            atomic(root/'basis/spec.json',spec)
            manifest=json.loads((root/'basis/manifest.json').read_text())
            manifest['spec']=reference(root/'basis/spec.json');atomic(root/'basis/manifest.json',manifest)
            stage=json.loads((root/'result.json').read_text())
            stage['basis_manifest']=reference(root/'basis/manifest.json');atomic(root/'result.json',stage)
            return result
        def restore_after_snapshot(*args):
            result=original_build(*args)
            for target,content in saved.items():target.write_bytes(content)
            return result
        with patch.object(week_presentation,'enrich',side_effect=substitute),patch.object(
                week_presentation,'build',side_effect=restore_after_snapshot):
            with self.assertRaises(ValueError):complete(self.entry,self.output,client)
        self.assertEqual(2,client.calls)
        self.assertFalse((self.output/'presentation-bundle.json').exists())
        self.assertFalse((self.output/'presentation-input.json').exists())
        self.assertEqual(self.bundle,read_ref(read_ref(self.entry)['files']['bundle.json']))

    def test_index_replaced_during_reference_capture_cannot_rebind_source_or_mode(self):
        import shared_weeks
        from page_navigation import MODE
        target=Path(self.entry['path']);original=target.read_bytes()
        original_reference=shared_weeks.reference
        other=copy.deepcopy(self.source);other['user_email']='foreign@example.invalid'
        foreign_directory=self.root/'foreign-scoring'
        evaluate_week(other,foreign_directory,FixtureClient(),shared_root=self.shared)
        foreign=read_ref(accepted_reference(self.shared,other,foreign_directory))
        for scenario in ('source','mode'):
            with self.subTest(scenario=scenario):
                replacement=copy.deepcopy(foreign if scenario=='source' else read_ref(self.entry))
                if scenario=='mode':replacement['page_navigation_mode']=MODE
                replaced=[]
                def substitute(path):
                    if Path(path).resolve()==target and not replaced:
                        atomic(target,replacement);replaced.append(True)
                    return original_reference(path)
                try:
                    with patch.object(shared_weeks,'reference',side_effect=substitute):
                        with self.assertRaises(ValueError):accepted_reference(self.shared,self.source,self.directory)
                    self.assertTrue(replaced)
                finally:target.write_bytes(original)


if __name__ == '__main__':unittest.main()
