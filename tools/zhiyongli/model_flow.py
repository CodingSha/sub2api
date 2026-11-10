"""状态卷内统一模型配额与熔断；预留额度是保守预算，不冒充实测吞吐。"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


class ModelFlow:
    def __init__(self,root,config,now=time.time,read_only=False):
        self.root=Path(root)
        if not read_only:self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path=self.root/'model-flow.sqlite';self.config=config;self.now=now
        self.read_only=read_only
        expected={'version','budget_key','approved_by','reason','max_concurrency','requests_per_minute',
            'reserved_tokens_per_minute','max_requests','transient_failure_limit','cooldown_seconds'}
        if (set(config)-{'max_429_retries','max_interrupted_retries'}!=expected or config['version']!=1 or any(not isinstance(config[k],str) or not config[k].strip()
                for k in ('budget_key','approved_by','reason')) or any(type(config[k]) is not int or config[k]<1
                for k in expected-{'version','budget_key','approved_by','reason'})):
            raise ValueError('全局模型配额须显式指定范围、确认人与全部正整数限制')
        if type(config.get('max_429_retries',0)) is not int or not 0<=config.get('max_429_retries',0)<=3:
            raise ValueError('429续跑次数须显式设置为0至3；每次仍计入全部预算')
        if type(config.get('max_interrupted_retries',0)) is not int or not 0<=config.get('max_interrupted_retries',0)<=3:
            raise ValueError('传输中断续发次数须显式设置为0至3；每次仍计入全部预算')
        if read_only:
            if not self.path.exists():raise ValueError('全局模型账簿不存在')
            return
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS attempts(
                id TEXT PRIMARY KEY,budget_key TEXT NOT NULL,request_sha TEXT NOT NULL,receipt TEXT NOT NULL,
                started REAL NOT NULL,reserved_tokens INTEGER NOT NULL,status TEXT NOT NULL,
                finished REAL,actual_tokens INTEGER,error TEXT,owner_pid INTEGER);
                CREATE TABLE IF NOT EXISTS circuit(id INTEGER PRIMARY KEY CHECK(id=1),until_at REAL NOT NULL,failures INTEGER NOT NULL);
                INSERT OR IGNORE INTO circuit VALUES(1,0,0);''')
            if 'owner_pid' not in {r[1] for r in db.execute('PRAGMA table_info(attempts)')}:
                db.execute('ALTER TABLE attempts ADD COLUMN owner_pid INTEGER')
            from flow_measurements import initialize
            initialize(db)

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=10) if self.read_only else sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:yield db
        finally:db.close()

    def recover(self,db,now):
        # 预留后进程崩溃不能占死并发。只回收已落盘终态，或超时的孤儿预留；绝不重新发送。
        rows=db.execute("SELECT * FROM attempts WHERE status='active'").fetchall()
        for row in rows:
            path=Path(row['receipt']);record=json.loads(path.read_text()) if path.exists() else {}
            if record.get('status') in {'received','unknown','rejected'}:
                self.finish(db,row['id'],record,now)
            elif now-row['started']>1860 and row['owner_pid']:
                # 网络读取可能超过名义总时限；活进程的慢请求不能按年龄回收并发。
                try:os.kill(row['owner_pid'],0)
                except ProcessLookupError:
                    self.finish(db,row['id'],dict(status='unknown',error='orphaned_send_intent'),now)

    def reconcile_startup(self,worker_lock):
        """取得同状态卷Worker独占锁后核对上次遗留，不依赖可能复用的容器PID。"""
        import fcntl
        expected=(self.root.parent/'worker.lock').resolve()
        if self.read_only or Path(worker_lock.name).resolve()!=expected:
            raise ValueError('启动核账必须持有同状态卷的Worker独占锁')
        fcntl.flock(worker_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute("SELECT * FROM attempts WHERE status='active'").fetchall():
                path=Path(row['receipt']);record=json.loads(path.read_text()) if path.exists() else {}
                if record.get('status') not in {'received','unknown','rejected'}:
                    record=dict(status='unknown',error='worker_restart_unresolved_send_intent')
                self.finish(db,row['id'],record,self.now())

    def finish(self,db,aid,record,now):
        old=db.execute('SELECT status FROM attempts WHERE id=?',(aid,)).fetchone()
        if not old or old['status']!='active':return
        status=record.get('status','unknown');error=record.get('error','')
        if status not in {'received','rejected','unknown','cancelled'}:status='unknown'
        response=record.get('response');usage=response.get('usage') if isinstance(response,dict) else None
        tokens=usage.get('total_tokens') if isinstance(usage,dict) else None
        if type(tokens) is not int or tokens<0:tokens=None
        db.execute('UPDATE attempts SET status=?,finished=?,actual_tokens=?,error=? WHERE id=?',
            (status,now,tokens,error,aid))
        from flow_measurements import save
        save(db,aid,record)
        if status=='received':
            db.execute('UPDATE circuit SET failures=0 WHERE id=1')
        elif status=='unknown' or error=='http_429':
            row=db.execute('SELECT * FROM circuit WHERE id=1').fetchone();failures=row['failures']+1
            until=row['until_at']
            if error=='http_429' or failures>=self.config['transient_failure_limit']:
                until=max(until,now+self.config['cooldown_seconds'],record.get('retry_after_at',0))
            db.execute('UPDATE circuit SET failures=?,until_at=? WHERE id=1',(failures,until))

    @contextmanager
    def slot(self,request_sha,receipt,body,max_output):
        from pipeline import Paused
        # UTF-8字节加最大输出是保守预留，不宣称等于模型tokenizer的实际用量。
        reserve=len(body)+max_output;now=self.now();c=self.config;aid=uuid.uuid4().hex
        if reserve>c['reserved_tokens_per_minute']:
            raise Paused('needs_review','单请求保守预留超过全局每分钟预算；须分批或核对已获配额，不截断原文')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');self.recover(db,now)
            active=db.execute("SELECT count(*) FROM attempts WHERE status='active'").fetchone()[0]
            recent=db.execute("SELECT count(*),coalesce(sum(coalesce(actual_tokens,reserved_tokens)),0) FROM attempts WHERE (started>? OR status='active') AND status!='cancelled'",(now-60,)).fetchone()
            total=db.execute("SELECT count(*) FROM attempts WHERE budget_key=? AND status!='cancelled'",(c['budget_key'],)).fetchone()[0]
            circuit=db.execute('SELECT until_at FROM circuit WHERE id=1').fetchone()[0]
            # 配额等待不占用任务预算；事务必须先提交恢复结果，再返回等待。
            reason=None;state='queued'
            if total>=c['max_requests']:state='needs_review';reason='全局模型请求预算已用尽，须核账后继续'
            elif circuit>now:reason='模型服务处于限流或异常冷却期，已有回执保留'
            elif active>=c['max_concurrency']:reason='模型并发配额已满，等待其他任务落盘后继续'
            elif recent[0]>=c['requests_per_minute'] or recent[1]+reserve>c['reserved_tokens_per_minute']:
                reason='达到全局每分钟请求或保守token预留预算，等待配额窗口'
            if not reason:
                db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,NULL,NULL,NULL,?)',
                    (aid,c['budget_key'],request_sha,str(Path(receipt).resolve()),now,reserve,'active',os.getpid()))
        if reason:raise Paused(state,reason)
        entered=False
        try:
            yield
            entered=True
        finally:
            record=json.loads(Path(receipt).read_text()) if Path(receipt).exists() else {}
            # 本地预算拒绝发生在实际发送之前；确定未发送才能释放预留计数。
            if not entered and not record:
                import sys
                ex=sys.exc_info()[1]
                if isinstance(ex,Paused) and ex.state in {'queued','needs_review'}:record=dict(status='cancelled')
            if not record:record=dict(status='unknown',error='missing_receipt_after_reservation')
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE');self.finish(db,aid,record,self.now())

    def snapshot(self):
        with self.connect() as db:
            from flow_measurements import snapshot
            db.execute('BEGIN')
            rows=[dict(row) for row in db.execute('SELECT status,count(*) AS count FROM attempts GROUP BY status')]
            measured=db.execute('SELECT count(*),sum(actual_tokens),max(finished-started) FROM attempts WHERE actual_tokens IS NOT NULL').fetchone()
            used=db.execute("SELECT count(*) FROM attempts WHERE budget_key=? AND status!='cancelled'",(self.config['budget_key'],)).fetchone()[0]
            return dict(budget_key=self.config['budget_key'],states=rows,measured_usage_requests=measured[0],
                actual_tokens=measured[1],longest_measured_seconds=measured[2],
                longest_measured_seconds_meaning='既有字段：预留到终态落账的墙钟间隔，可能包含重启后核账等待，不可当作API延迟。',
                request_budget=dict(used=used,limit=self.config['max_requests'],remaining=max(0,self.config['max_requests']-used)),
                measurements=snapshot(db,self.config['budget_key']),
                circuit=dict(db.execute('SELECT until_at,failures FROM circuit WHERE id=1').fetchone()))

    def blocked(self):
        """轻量状态检查；只调节轮询等待，不能拦住无需新请求的候选接入。"""
        now=self.now();c=self.config
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');self.recover(db,now)
            circuit=db.execute('SELECT until_at FROM circuit WHERE id=1').fetchone()[0]
            total=db.execute("SELECT count(*) FROM attempts WHERE budget_key=? AND status!='cancelled'",(c['budget_key'],)).fetchone()[0]
            active=db.execute("SELECT count(*) FROM attempts WHERE status='active'").fetchone()[0]
            recent=db.execute("SELECT count(*),coalesce(sum(coalesce(actual_tokens,reserved_tokens)),0) FROM attempts WHERE (started>? OR status='active') AND status!='cancelled'",(now-60,)).fetchone()
            if total>=c['max_requests']:return 'global_budget_exhausted'
            if circuit>now:return 'service_cooldown'
            if active>=c['max_concurrency']:return 'concurrency_full'
            if recent[0]>=c['requests_per_minute'] or recent[1]>=c['reserved_tokens_per_minute']:return 'minute_quota_full'
            return None
