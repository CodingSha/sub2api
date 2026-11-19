"""采集并发有界、异常释放连接，不持有空闲来源会话。"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import threading
import time
import unittest
import os
import uuid
from unittest.mock import patch

from worker import source_session


class SourceSessionTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('ZHIYONGLI_TEST_DSN'), '需要显式本地专用测试库')
    def test_real_eight_product_sessions_and_one_source_session(self):
        import psycopg
        from psycopg.conninfo import make_conninfo
        dsn=os.environ['ZHIYONGLI_TEST_DSN'];tag='zy_eight_'+uuid.uuid4().hex
        slots=threading.BoundedSemaphore(1);ready=threading.Barrier(8);first_failed=threading.Event()
        product=make_conninfo(dsn,application_name=tag+'_product')
        source=make_conninfo(dsn,application_name=tag+'_source')
        class SyntheticFailure(ValueError):pass
        def work(index):
            with psycopg.connect(product,autocommit=True):
                ready.wait(timeout=10)
                if index and not first_failed.wait(timeout=10):raise AssertionError('异常采集线程未完成')
                try:
                    with source_session(source,slots) as db:
                        counts=db.execute('SELECT count(*) FILTER (WHERE application_name=%s),count(*) FILTER (WHERE application_name=%s) FROM pg_stat_activity',(tag+'_product',tag+'_source')).fetchone()
                        time.sleep(.02)
                        if index==0:raise SyntheticFailure('合成采集失败')
                        return counts
                # 异常也须释放来源连接，但不能丢弃首个真实并发观测。
                except SyntheticFailure:first_failed.set();return counts
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(work,range(8)))
        self.assertEqual(8,results[0][0])
        self.assertEqual(max(s for p,s in results),1)
        self.assertLessEqual(max(p+s for p,s in results),9)
        with psycopg.connect(dsn) as db:
            self.assertEqual(0,db.execute('SELECT count(*) FROM pg_stat_activity WHERE application_name IN (%s,%s)',(tag+'_product',tag+'_source')).fetchone()[0])

    def test_six_consumers_use_at_most_two_connections_and_close_on_failure(self):
        slots=threading.BoundedSemaphore(2)
        lock=threading.Lock(); counts=dict(active=0,peak=0,closed=0)
        @contextmanager
        def connect(*args,**kwargs):
            with lock:
                counts['active']+=1
                counts['peak']=max(counts['peak'],counts['active'])
            try:yield object()
            finally:
                with lock:
                    counts['active']-=1;counts['closed']+=1
        def collect(index):
            try:
                with source_session('test-only',slots):
                    time.sleep(.01)
                    if index==0:raise ValueError('合成采集失败')
            except ValueError:pass
        with patch('psycopg.connect',side_effect=connect):
            with ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(collect,range(6)))
            self.assertEqual(dict(active=0,peak=2,closed=6),counts)
            with source_session('test-only',slots):pass
        self.assertEqual(7,counts['closed'])
