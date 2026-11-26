"""验证清理边界、归档失败保护，以及容器状态变化时不误删。"""
import copy
import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import zhiyongli_containers as lifecycle


def fixture():
    return dict(Id='a' * 64, Name='/zhiyongli-test', Image='sha256:worker',
                Config=dict(Image='zhiyongli-worker-local:fixture', Env=['SECRET=private']),
                HostConfig=dict(AutoRemove=False, RestartPolicy=dict(Name='no')),
                State=dict(Status='exited', Running=False, FinishedAt='2026-11-20T01:00:00.000Z'),
                Mounts=[dict(Type='bind', Source='/data/zhiyongli/state', Destination='/state')])


class ContainerCleanupTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.datetime(2026, 11, 26, tzinfo=datetime.timezone.utc).timestamp()

    def test_old_worker_recognized_by_immutable_image_tags(self):
        row = fixture()
        row['Config']['Image'] = 'old-image-id'
        self.assertTrue(lifecycle.eligible(row, {'sha256:worker': ['zhiyongli-worker-local:old']}, self.now, 600))

    def test_never_select_database_or_name_only_match(self):
        row = fixture()
        row['Config']['Image'] = 'postgres:18'
        self.assertFalse(lifecycle.eligible(row, {}, self.now, 600))

    def test_never_select_running_or_persistent_worker(self):
        row = fixture()
        row['State'].update(Status='running', Running=True)
        self.assertFalse(lifecycle.eligible(row, {}, self.now, 600))
        row = fixture()
        row['HostConfig']['RestartPolicy']['Name'] = 'unless-stopped'
        self.assertFalse(lifecycle.eligible(row, {}, self.now, 600))

    def test_other_owner_and_persistent_label_override_legacy_image(self):
        for labels in [{lifecycle.OWNER: 'other'}, {lifecycle.ROLE: 'persistent'}]:
            row = fixture()
            row['Config']['Labels'] = labels
            self.assertFalse(lifecycle.eligible(row, {}, self.now, 600))

    def test_recent_exit_and_invalid_time_remain(self):
        row = fixture()
        for stamp in ['2026-11-25T23:59:59Z', 'invalid']:
            row['State']['FinishedAt'] = stamp
            self.assertFalse(lifecycle.eligible(row, {}, self.now, 600))

    def test_metadata_does_not_include_credentials(self):
        self.assertNotIn('SECRET', json.dumps(lifecycle.metadata(fixture())))

    def test_dry_run_makes_no_mutating_calls(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(lifecycle, 'docker') as docker:
            report = lifecycle.cleanup([fixture()], {}, Path(directory))
            self.assertEqual(len(report['preserved']), 1)
            docker.assert_not_called()

    def test_archive_failure_never_removes_container(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(lifecycle, 'archive_logs', side_effect=OSError), patch.object(lifecycle, 'docker') as docker:
            report = lifecycle.cleanup([fixture()], {}, Path(directory), apply=True)
            self.assertEqual(len(report['failures']), 1)
            docker.assert_not_called()

    def test_restart_during_archive_preserves_container(self):
        restarted = copy.deepcopy(fixture())
        restarted['State'].update(Status='running', Running=True)
        with tempfile.TemporaryDirectory() as directory, patch.object(lifecycle, 'archive_logs', return_value={}), patch.object(lifecycle, 'inspect_many', return_value=[restarted]), patch.object(lifecycle, 'docker') as docker:
            report = lifecycle.cleanup([fixture()], {}, Path(directory), apply=True)
            self.assertEqual(len(report['preserved']), 1)
            docker.assert_not_called()

    def test_only_removes_exact_stopped_id_without_force_or_volumes(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(lifecycle, 'archive_logs', return_value={}), patch.object(lifecycle, 'inspect_many', return_value=[fixture()]), patch.object(lifecycle, 'docker') as docker:
            report = lifecycle.cleanup([fixture()], {}, Path(directory), apply=True)
            self.assertEqual(report['removed'], ['a' * 64])
            docker.assert_called_once_with('rm', 'a' * 64)


if __name__ == '__main__':
    unittest.main()
