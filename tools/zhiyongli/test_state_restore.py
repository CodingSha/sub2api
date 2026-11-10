"""停止写入后的状态卷备份演练；仅合成来源，恢复到原挂载路径，不访问模型。"""
import tarfile
import unittest

from context_navigation import Navigation,check_cached_receipt
from model_flow import ModelFlow
from pipeline import received,Paused
from preparation import Preparations
from runtime import atomic,digest
from source_snapshot import ObjectCache
from worker import JobClient
import test_context_navigation as navigation_fixtures
import test_reading_cache as reading_fixtures


class StateRestoreTests(unittest.TestCase):
    setUp=reading_fixtures.ReadingCacheTests.setUp

    def test_stopped_volume_restore_keeps_shared_receipts_hardlinks_and_send_budgets(self):
        state=self.root/'state';state.mkdir(mode=0o700)
        config=dict(version=1,budget_key='synthetic-restore',approved_by='本地演练',reason='合成恢复',
            max_concurrency=4,requests_per_minute=100,reserved_tokens_per_minute=1000000,
            max_requests=100,transient_failure_limit=2,cooldown_seconds=60)
        flow=ModelFlow(state/'flow',config)
        client=JobClient(self.config,state/'daily',100,100,flow)
        client.navigation_cache_root=state/'navigation-branches'
        packet=navigation_fixtures.NavigationTests().packet(33)
        notes=navigation_fixtures.NavigationTests().notes(packet)
        nav=Navigation(client.root/'navigation',packet);nav.build(notes,client,received)
        pending=state/'unfinished/original.receipt.json'
        interrupted=JobClient(self.config,state/'unfinished',100,100,flow)
        payload=dict(input='合成待核原请求')
        request=digest(dict(endpoint=self.config.responses_url,payload=payload))
        interrupted.budget.reserve(pending,request)
        atomic(pending,dict(status='requesting',request_sha256=request))
        with flow.connect() as db:
            db.execute("INSERT INTO attempts VALUES('interrupted','synthetic-restore',?,?,0,10,'active',NULL,NULL,NULL,1)",(request,str(pending)))
        queue=Preparations(state)
        with queue.connect() as db:
            db.execute("INSERT INTO preparations VALUES('pending',1,'synthetic@example.test','{}','2026-09-08',NULL,'unknown',0,'合成待核')")
        cache=ObjectCache(state/'source-cache');sha=cache.put('synthetic-revision',dict(text='合成完整来源，不丢失'))
        cache.attach(sha,state/'sources');cache.db.close()
        # 所有数据库连接已关闭且无Worker写入；真实部署还须协调产品库快照及发送时点。
        archive=self.root/'private-state.tar.gz'
        with tarfile.open(archive,'w:gz') as bundle:bundle.add(state,arcname='state')
        archive.chmod(0o600)
        before=flow.snapshot();sent=self.calls
        original=self.root/'original-quarantined';state.rename(original)
        # 仅解压本测试刚生成的受控归档，不是面向外部备份文件的解压入口。
        with tarfile.open(archive) as bundle:bundle.extractall(self.root)
        self.assertNotEqual((original/'source-cache/objects'/(sha+'.json')).stat().st_ino,
            (state/'source-cache/objects'/(sha+'.json')).stat().st_ino)
        self.assertEqual((state/'source-cache/objects'/(sha+'.json')).stat().st_ino,
            (state/'sources/objects'/(sha+'.json')).stat().st_ino)
        restored=ModelFlow(state/'flow',config)
        self.assertEqual(before,restored.snapshot())
        with (state/'worker.lock').open('a') as lock:restored.reconcile_startup(lock)
        self.assertIn(dict(status='unknown',count=1),restored.snapshot()['states'])
        self.assertEqual('unknown',Preparations(state).snapshot()[0]['state'])
        resumed=JobClient(self.config,state/'unfinished',100,100,restored)
        self.assertEqual(1,resumed.budget.used())
        self.assertEqual('requesting',resumed._call(payload,pending)['status'])
        pending.unlink()
        with self.assertRaises(Paused) as caught:resumed._call(payload,pending)
        self.assertEqual('unknown',caught.exception.state)
        for proof in (state/'daily/navigation').glob('*.cache.json'):check_cached_receipt(proof)
        frozen=JobClient(self.config,state/'frozen',100,100,restored)
        frozen.navigation_cache_root=state/'navigation-branches'
        Navigation(frozen.root/'navigation',packet).build(notes,frozen,received)
        self.assertEqual((sent,0),(self.calls,frozen.budget.used()))
        restored_cache=ObjectCache(state/'source-cache')
        try:
            self.assertEqual(sha,restored_cache.get('synthetic-revision'))
            (state/'source-cache/objects'/(sha+'.json')).write_text('合成恢复后损坏')
            with self.assertRaises(ValueError):restored_cache.get('synthetic-revision')
        finally:restored_cache.db.close()
