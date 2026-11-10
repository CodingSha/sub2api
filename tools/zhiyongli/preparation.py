"""工作日常增量来源与长材料导航准备；只有封口任务可生成评分。"""
from contextlib import contextmanager
from datetime import date,datetime,time,timedelta
import fcntl
import json
from pathlib import Path
import sqlite3
import shutil
import time as clock

from runtime import atomic,digest
from work_calendar import TZ


class Preparations:
    def __init__(self,state):
        self.root=Path(state);self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path=self.root/'preparations.sqlite'
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS preparations(
                key TEXT PRIMARY KEY,user_id INTEGER NOT NULL,user_email TEXT NOT NULL,scope TEXT NOT NULL,
                through_date TEXT NOT NULL,prepared_through TEXT,state TEXT NOT NULL,updated REAL NOT NULL,message TEXT NOT NULL)''')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row;db.execute('PRAGMA synchronous=FULL')
        try:
            with db:yield db
        finally:db.close()

    def enqueue(self,daily):
        if not daily:return
        from workday_scope import validate
        from personal_scope import excluded_user_ids
        excluded = excluded_user_ids()
        scope=validate(daily['workday_scope']);day=date.fromisoformat(daily['through_date'])
        if not scope['period_start']<=str(day)<scope['period_end']:raise ValueError('日常准备截止日不在本周范围')
        for user in daily['users']:
            if user['user_id'] in excluded:
                continue
            key=digest(dict(user=user,scope=scope))
            with self.connect() as db:
                db.execute('''INSERT INTO preparations VALUES(?,?,?,?,?,NULL,'queued',?,'等待增量准备')
                    ON CONFLICT(key) DO UPDATE SET through_date=max(through_date,excluded.through_date),
                    state=CASE WHEN state='prepared' AND through_date<excluded.through_date THEN 'queued' ELSE state END''',
                    (key,user['user_id'],user['user_email'],json.dumps(scope,ensure_ascii=False),str(day),clock.time()))

    def update(self,key,state,message,prepared=None):
        with self.connect() as db:
            if prepared is not None:
                db.execute("""UPDATE preparations SET prepared_through=?,state=CASE WHEN through_date>? THEN 'queued' ELSE 'prepared' END,
                    updated=?,message=? WHERE key=?""",(prepared,prepared,clock.time(),message,key))
            else:db.execute('UPDATE preparations SET state=?,updated=?,message=? WHERE key=?',(state,clock.time(),message,key))

    def snapshot(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT state,count(*) AS count,min(updated) AS oldest_update FROM preparations GROUP BY state')]

    def tick(self,source,base_client,excluded_keys,max_requests=100,new_request_limit=1):
        from source_snapshot import collect,require_current_query
        from source_frames import read_signed
        from worker import JobClient
        from pipeline import prepare,Paused,DIRECT_BYTES,reading_view
        from bounded_read import raw_size
        with self.connect() as db:rows=db.execute("SELECT * FROM preparations WHERE state IN ('queued','running') ORDER BY updated,key LIMIT 100").fetchall()
        for row in rows:
            root=self.root/'preparations'/row['key'];root.mkdir(parents=True,exist_ok=True,mode=0o700)
            with (root/'prepare.lock').open('a') as lock:
                try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:continue
                with self.connect() as db:
                    current=db.execute('SELECT * FROM preparations WHERE key=?',(row['key'],)).fetchone()
                if current['state'] not in {'queued','running'}:continue
                row=dict(current);scope=json.loads(row['scope'])
                self.update(row['key'],'running','只读维护来源和长材料阅读导航，不生成分数')
                try:
                    from personal_scope import require_personal_user
                    require_personal_user(row['user_id'])
                    client=JobClient(base_client.config,root,max_requests,new_request_limit,getattr(base_client,'flow',None))
                    client.reading_cache_root=self.root/'read-pages';client.reading_plan_root=self.root/'reading-plans'
                    client.navigation_cache_root=self.root/'navigation-branches'
                    source_root=root/'sources'/row['through_date']
                    if not (source_root/'latest.json').exists():
                        collect(source,source_root,excluded_keys,scope['period_start']+'T00:00:00+08:00',[row['user_id']],shared_root=self.root/'source-cache')
                    snapshot=read_signed(source_root,json.loads((source_root/'latest.json').read_text()))
                    person=next((u for u in snapshot['users'] if u['id']==row['user_id']),None)
                    if not person or person['email']!=row['user_email']:raise ValueError('日常准备来源账户映射不一致')
                    require_current_query(snapshot)
                    until=datetime.combine(date.fromisoformat(row['through_date'])+timedelta(days=1),time(),TZ).isoformat()
                    packet=prepare(source_root,row['user_id'],scope['period_start']+'T00:00:00+08:00',scope['period_end']+'T00:00:00+08:00',
                        scope,preparation=True,preparation_until=until)
                    atomic(root/'preparation-packet.json',dict(packet_sha256=digest(packet),through_date=row['through_date'],
                        preparation_only=True,source_snapshot=packet['source_snapshot']))
                    if raw_size(packet)>DIRECT_BYTES:
                        target=root/'prepared-views'/digest(packet)
                        view=reading_view(packet,target,client,lambda *args:None)
                        atomic(root/'prepared-navigation.json',dict(packet_sha256=digest(packet),through_date=row['through_date'],
                            navigation=view.get('context_navigation'),shared_cache=str(client.navigation_cache_root.resolve()),
                            limitation='仅记录日常局部导航；原回执和原文保留，可按完整来源重建，不是周评结果'))
                        # 日常树不被周评引用，完成后可从共享回执重建，避免每日复制整棵节点库。
                        # 未完成/unknown不进入这里，保留原执行目录供核账。
                        shutil.rmtree(target)
                    self.update(row['key'],'prepared','来源及必要长材料导航已准备；最终目标归并和评分仍等待封口',row['through_date'])
                except Paused as ex:self.update(row['key'],ex.state,str(ex))
                except Exception as ex:
                    atomic(root/'failure.json',dict(error_type=type(ex).__name__,at=clock.time()))
                    self.update(row['key'],'needs_review','日常来源或阅读合同未通过；保留记录待核')
                return True
        return False
