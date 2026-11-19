"""工作日常增量来源与长材料导航准备；只有封口任务可生成评分。"""
from contextlib import contextmanager
from datetime import date,datetime,time,timedelta
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import shutil
import time as clock

from runtime import atomic,digest,json_bytes
from work_calendar import TZ


def nightly_refresh_due(scope,through_date,now):
    """只在当晚23点至下一工作日8点间补查晚到/修订，使用已登记日历。"""
    from workday_scope import load
    target=date.fromisoformat(through_date)
    if now<datetime.combine(target,time(23),TZ):return False
    registry,_=load(expected=scope['registry_sha256'])
    later=sorted({d for week in registry['weeks'].values() for d in week['working_dates'] if d>through_date})
    if not later:raise ValueError('缺少下一工作日日历，不能猜测补偿截止时间')
    return now<datetime.combine(date.fromisoformat(later[0]),time(8),TZ)


def _packet_signature(packet):
    """同一编码流核原摘要及容量；键排序不改变原compact JSON总字节数。"""
    signature=hashlib.sha256();size=0
    for chunk in json_bytes(packet):
        signature.update(chunk);size+=len(chunk)
    return signature.hexdigest(),size


def prepared_packet(source_root,user_id,scope,until,metadata_cache,*,session_review=False):
    """轮转重用同一冻结材料；逐次核原件及压缩包，不常驻多用户正文。"""
    import gzip,io,os,uuid
    from disk_guard import require_space
    from pipeline import prepare
    from runtime import moment,policy
    from source_frames import read_signed
    from source_snapshot import file_sha256
    source_root=Path(source_root);latest=json.loads((source_root/'latest.json').read_text())
    snapshot=read_signed(source_root,latest)
    identity=dict(version='preparation-packet-cache-20261115.1',source_snapshot=latest,
        user_id=user_id,scope=scope,until=until,execution=policy())
    if session_review:identity['source_access_mode']='session_review'
    directory=source_root/'prepared-packets';key=digest(identity)
    manifest=directory/(key+'.manifest.json');cache=directory/(key+'.jsonl.gz')
    if manifest.exists():
        proof=json.loads(manifest.read_text())
        if proof.get('identity')!=identity:raise ValueError('日常材料缓存身份签名变化')
        # 缓存只节省协议展开，不能以来源路径/mtime替代逐次原件SHA核验。
        for ref in snapshot['rows']:
            path=(source_root/ref['path']).resolve()
            if not path.is_relative_to(source_root.resolve()) or file_sha256(path)!=ref['sha256']:
                raise ValueError('日常材料缓存的原始来源签名变化')
        if file_sha256(cache)!=proof['compressed_sha256']:raise ValueError('日常压缩材料缓存签名变化')
        with gzip.open(cache,'rt',encoding='utf-8') as stream:
            packet=json.loads(next(stream));packet['events']=[json.loads(line) for line in stream]
        signature,size=_packet_signature(packet)
        if signature!=proof['packet_sha256']:raise ValueError('日常材料缓存正文签名变化')
        if type(proof.get('packet_bytes')) is not int or proof['packet_bytes']!=size:raise ValueError('日常材料缓存容量签名变化')
        person=next((p for p in snapshot['users'] if p['id']==user_id),None)
        cutoff=min(moment(scope['observed_until']),moment(until),moment(snapshot['snapshot_at'])).isoformat()
        if (packet.get('preparation_only') is not True or packet.get('source_snapshot')!=latest
                or str(packet.get('user_id'))!=str(user_id) or not person or packet.get('user_email')!=person['email']
                or packet.get('workday_scope')!=scope or packet.get('preparation_observed_until')!=cutoff
                or packet.get('since')!=scope['period_start']+'T00:00:00+08:00'
                or packet.get('until')!=scope['period_end']+'T00:00:00+08:00'):
            raise ValueError('日常材料缓存的人员、范围或准备身份变化')
        return packet,signature,size
    if cache.exists():raise ValueError('日常材料缓存未封口，保留待核，不能覆盖')
    packet=prepare(source_root,user_id,scope['period_start']+'T00:00:00+08:00',scope['period_end']+'T00:00:00+08:00',
        scope,preparation=True,preparation_until=until,metadata_cache=metadata_cache,session_review=session_review)
    if packet['source_snapshot']!=latest:raise ValueError('日常装配期间来源快照变化，不能缓存不同输入')
    signature,size=_packet_signature(packet);require_space(source_root,size)
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    temporary=cache.with_name(cache.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with os.fdopen(os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as raw:
            # 每行只解析单事件，避免json.load完整包造成整段Unicode正文副本。
            with gzip.GzipFile(filename='',mode='wb',fileobj=raw,compresslevel=1,mtime=0) as compressed:
                with io.TextIOWrapper(compressed,encoding='utf-8') as stream:
                    stream.write(json.dumps({k:v for k,v in packet.items() if k!='events'},ensure_ascii=False,separators=(',',':'),sort_keys=True)+'\n')
                    for event in packet['events']:
                        stream.write(json.dumps(event,ensure_ascii=False,separators=(',',':'),sort_keys=True)+'\n')
            raw.flush();os.fsync(raw.fileno())
        os.replace(temporary,cache)
        atomic(manifest,dict(identity=identity,compressed_sha256=file_sha256(cache),packet_sha256=signature,packet_bytes=size))
    finally:
        temporary.unlink(missing_ok=True)
    return packet,signature,size


class Preparations:
    def __init__(self,state):
        self.root=Path(state);self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path=self.root/'preparations.sqlite'
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS preparations(
                key TEXT PRIMARY KEY,user_id INTEGER NOT NULL,user_email TEXT NOT NULL,scope TEXT NOT NULL,
                through_date TEXT NOT NULL,prepared_through TEXT,state TEXT NOT NULL,updated REAL NOT NULL,message TEXT NOT NULL)''')
            if 'mode' not in {r[1] for r in db.execute('PRAGMA table_info(preparations)')}:
                db.execute("ALTER TABLE preparations ADD COLUMN mode TEXT NOT NULL DEFAULT 'navigation'")
            columns={r[1] for r in db.execute('PRAGMA table_info(preparations)')}
            for name,kind in (('source_collected_through','TEXT'),('source_updated','REAL NOT NULL DEFAULT 0'),
                              ('refresh_requested','INTEGER NOT NULL DEFAULT 0')):
                if name not in columns:db.execute('ALTER TABLE preparations ADD COLUMN '+name+' '+kind)

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
        mode=daily.get('mode','navigation')
        if mode not in {'navigation','reading_only','reading_prefix','session_facts'}:raise ValueError('日常准备模式无效')
        if not scope['period_start']<=str(day)<scope['period_end']:raise ValueError('日常准备截止日不在本周范围')
        for user in daily['users']:
            if user['user_id'] in excluded:
                continue
            identity=dict(user=user,scope=scope)
            if mode=='session_facts':identity['mode']=mode
            key=digest(identity)
            with self.connect() as db:
                prior=db.execute('SELECT mode FROM preparations WHERE key=?',(key,)).fetchone()
                if prior is not None and prior['mode']!=mode:
                    raise ValueError('日常准备模式发生变化，保留原记录和账簿，须核对后显式迁移')
                db.execute('''INSERT INTO preparations(key,user_id,user_email,scope,through_date,prepared_through,state,updated,message,mode) VALUES(?,?,?,?,?,NULL,'queued',?,'等待增量准备',?)
                    ON CONFLICT(key) DO UPDATE SET through_date=max(through_date,excluded.through_date),
                    state=CASE WHEN state='prepared' AND through_date<excluded.through_date THEN 'queued' ELSE state END''',
                    (key,user['user_id'],user['user_email'],json.dumps(scope,ensure_ascii=False),str(day),clock.time(),mode))
                if mode=='session_facts' and nightly_refresh_due(scope,str(day),datetime.fromtimestamp(clock.time(),TZ)):
                    db.execute("""UPDATE preparations SET state='queued',refresh_requested=1,message='补查晚到响应及来源修订'
                        WHERE key=? AND state='prepared' AND source_updated<?""",(key,clock.time()-3600))

    def update(self,key,state,message,prepared=None):
        with self.connect() as db:
            if prepared is not None:
                db.execute("""UPDATE preparations SET prepared_through=?,state=CASE WHEN through_date>? THEN 'queued' ELSE 'prepared' END,
                    updated=?,message=? WHERE key=?""",(prepared,prepared,clock.time(),message,key))
            else:db.execute('UPDATE preparations SET state=?,updated=?,message=? WHERE key=?',(state,clock.time(),message,key))

    def snapshot(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT state,count(*) AS count,min(updated) AS oldest_update,min(through_date) AS oldest_target_date,max(through_date) AS latest_target_date,min(prepared_through) AS earliest_prepared_through FROM preparations GROUP BY state')]


    def ready_budget(self,job,*,session_review=False):
        from pipeline import Paused
        scope=job['workday_scope']
        identity=dict(user=dict(user_id=job['user_id'],user_email=job['user_email']),scope=scope)
        if session_review:identity['mode']='session_facts'
        key=digest(identity)
        with self.connect() as db:row=db.execute('SELECT * FROM preparations WHERE key=?',(key,)).fetchone()
        required=scope['working_dates'][-1] if scope['working_dates'] else None
        if required is None:return None
        if row is None:raise Paused('queued','等待本周每日来源准备登记后续评')
        if row['state'] in {'unknown','needs_review','failed'}:
            raise Paused(row['state'],'本人的日常预处理仍待核对；原回执保留，周评未另起重复阅读')
        if row['state']!='prepared' or not row['prepared_through'] or row['prepared_through']<required:
            raise Paused('queued','等待本人最后一个工作日预处理完成后续评')
        path=self.root/'preparations'/key/'request-budget.json'
        value=json.loads(path.read_text())
        if value.get('version')!=1 or not isinstance(value.get('entries'),dict):raise ValueError('日常请求预算无效，不能当作零次')
        return dict(path=str(path.resolve()),sha256=digest(value),used=len(value['entries']))

    def fact_reference(self,job):
        identity=dict(user=dict(user_id=job['user_id'],user_email=job['user_email']),scope=job['workday_scope'],mode='session_facts')
        path=self.root/'preparations'/digest(identity)/'session-facts-latest.json'
        if not path.exists():
            from pipeline import Paused
            raise Paused('queued','等待本人每日事实封口后接续周期评审')
        ref=json.loads(path.read_text());source=Path(ref['directory'])
        if digest(json.loads((source/'facts-completed.json').read_text()))!=ref['facts_sha256']:
            raise ValueError('每日事实指针与封口签名不符')
        return source

    def collect_source(self,row,source,excluded_keys):
        """来源获取独立于模型状态；只在原件核验完成后推进采集水位。"""
        from source_snapshot import collect,require_current_query,file_sha256
        from source_frames import read_signed
        from pipeline import Paused
        from personal_scope import require_personal_user
        require_personal_user(row['user_id'])
        scope=json.loads(row['scope']);root=self.root/'preparations'/row['key']
        source_root=root/'sources'/row['through_date']
        until=(datetime.combine(date.fromisoformat(row['through_date']),time(21),TZ) if row['mode']=='session_facts' else
            datetime.combine(date.fromisoformat(row['through_date'])+timedelta(days=1),time(),TZ)).isoformat()
        def gather():
            collect(source,source_root,excluded_keys,scope['period_start']+'T00:00:00+08:00',
                [row['user_id']],shared_root=self.root/'source-cache')
        def verify():
            latest=json.loads((source_root/'latest.json').read_text())
            snapshot=read_signed(source_root,latest);require_current_query(snapshot)
            from runtime import moment
            if snapshot.get('since') and moment(snapshot['since'])>moment(scope['period_start']+'T00:00:00+08:00'):
                raise ValueError('来源缓存的采集起点晚于本期开始')
            if snapshot.get('until') and moment(snapshot['until'])<min(moment(scope['observed_until']),moment(until)):
                raise ValueError('来源缓存的采集终点早于本次截止')
            person=next((u for u in snapshot['users'] if u['id']==row['user_id']),None)
            if not person or person['email']!=row['user_email']:raise ValueError('日常准备来源账户映射不一致')
            for ref in snapshot['rows']:
                path=(source_root/ref['path']).resolve()
                if not path.is_relative_to(source_root.resolve()) or file_sha256(path)!=ref['sha256']:
                    raise ValueError('日常来源原件签名变化')
            return latest,snapshot
        if not (source_root/'latest.json').exists():gather()
        elif row.get('refresh_requested'):
            gather()
        latest,snapshot=verify()
        if datetime.fromisoformat(snapshot['snapshot_at'])<datetime.fromisoformat(until):
            gather();latest,snapshot=verify()
            if datetime.fromisoformat(snapshot['snapshot_at'])<datetime.fromisoformat(until):
                raise Paused('queued','来源快照尚未覆盖完整截止日，保留材料等待核对，不标记日常完成')
        atomic(root/'source-collection'/ (row['through_date']+'.json'),dict(through_date=row['through_date'],
            source_snapshot=latest,snapshot_at=snapshot['snapshot_at'],rows=len(snapshot['rows']),at=clock.time()))
        with self.connect() as db:
            db.execute("UPDATE preparations SET source_collected_through=max(coalesce(source_collected_through,''),?),source_updated=?,refresh_requested=0 WHERE key=?",
                (row['through_date'],clock.time(),row['key']))
        return source_root,until

    def tick(self,source,base_client,excluded_keys,max_requests=100,new_request_limit=1):
        from worker import JobClient
        from pipeline import Paused,DIRECT_BYTES,reading_view,reading_failure
        from bounded_read import read_all
        with self.connect() as db:
            rows=db.execute("""SELECT * FROM preparations WHERE state IN ('queued','running') OR
                (state IN ('unknown','needs_review','failed') AND coalesce(source_collected_through,'')<through_date
                 AND source_updated<?) ORDER BY CASE WHEN state IN ('queued','running') THEN updated ELSE source_updated END,key LIMIT 100""",
                (clock.time()-60,)).fetchall()
        for row in rows:
            root=self.root/'preparations'/row['key'];root.mkdir(parents=True,exist_ok=True,mode=0o700)
            with (root/'prepare.lock').open('a') as lock:
                try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:continue
                with self.connect() as db:
                    current=db.execute('SELECT * FROM preparations WHERE key=?',(row['key'],)).fetchone()
                row=dict(current);scope=json.loads(row['scope'])
                if row['state']=='failed' and row['mode']=='session_facts':
                    from execution_recovery import reserve
                    if reserve(root):
                        self.update(row['key'],'queued','按原阶段接续来源连接故障')
                        row['state']='queued'
                if row['state'] not in {'queued','running'}:
                    if (row['state'] not in {'unknown','needs_review','failed'}
                            or (row['source_collected_through'] or '')>=row['through_date']
                            or row['source_updated']>=clock.time()-60):continue
                    # 失败也记尝试时刻，至少轮转60秒；不动分析状态、错误或模型证据。
                    with self.connect() as db:
                        db.execute('UPDATE preparations SET source_updated=? WHERE key=?',(clock.time(),row['key']))
                    try:self.collect_source(row,source,excluded_keys)
                    except Exception as ex:
                        import traceback
                        atomic(root/'source-collection'/(row['through_date']+'.failure.json'),
                            dict(through_date=row['through_date'],error_type=type(ex).__name__,traceback=traceback.format_exc(),at=clock.time()))
                    return True
                self.update(row['key'],'running','只读维护来源和长材料阅读导航，不生成分数')
                execution_phase='collecting'
                try:
                    from personal_scope import require_personal_user
                    require_personal_user(row['user_id'])
                    client=JobClient(base_client.config,root,max_requests,new_request_limit,getattr(base_client,'flow',None),state_root=self.root)
                    client.reading_cache_root=self.root/'read-pages';client.reading_plan_root=self.root/'reading-plans'
                    client.navigation_cache_root=self.root/'navigation-branches'
                    client.reading_concurrency=getattr(base_client,'reading_concurrency',1)
                    client.reading_content_identity=row['mode'] in {'reading_only','reading_prefix'}
                    client.reading_efficiency=row['mode'] in {'reading_only','reading_prefix'}
                    client.reading_page_navigation=getattr(base_client,'reading_page_navigation',False) is True
                    client.session_review=row['mode']=='session_facts'
                    contract=root/'reading-contract.json'
                    if client.reading_page_navigation or contract.exists():
                        from page_navigation import MODE
                        from navigation_access import freeze
                        selected=dict(page_navigation_mode=MODE if client.reading_page_navigation else None)
                        if not contract.exists() and client.budget.used():
                            raise Paused('needs_review','日常准备已有原模式模型请求，不能在同一执行中开启实验页导航')
                        freeze(contract,selected)
                    source_root,until=self.collect_source(row,source,excluded_keys)
                    execution_phase='understanding'
                    packet,packet_sha,packet_bytes=prepared_packet(source_root,row['user_id'],scope,until,self.root/'source-frames',
                        **(dict(session_review=True) if client.session_review else {}))
                    atomic(root/'preparation-packet.json',dict(packet_sha256=packet_sha,through_date=row['through_date'],
                        preparation_only=True,source_snapshot=packet['source_snapshot']))
                    if client.session_review:
                        from session_review import evaluate
                        target=root/'session-facts'/packet_sha
                        prior=root/'session-facts-latest.json'
                        if prior.exists():
                            ref=json.loads(prior.read_text())
                            if ref['directory']!=str(target.resolve()):client.session_fact_from=ref['directory']
                        evaluate(packet,target,client,lambda *args:None)
                        atomic(prior,dict(directory=str(target.resolve()),through_date=row['through_date'],
                            facts_sha256=digest(json.loads((target/'facts-completed.json').read_text()))))
                    elif packet_bytes>DIRECT_BYTES and row['mode'] in {'reading_only','reading_prefix'}:
                        # 每日首读；受控前缀模式仅前置稳定闭组，不构建临时日根或全周归属。
                        status=read_all(packet,root/'reading',client=client,limit=None)
                        atomic(root/'prepared-reading.json',dict(packet_sha256=packet_sha,through_date=row['through_date'],status=status))
                        if status['state']=='reading_blocked':
                            raise reading_failure(status)
                        if len(status['received_pages'])!=status['pages']:raise Paused('queued','日常阅读继续轮转')
                        if row['mode']=='reading_prefix':
                            from hierarchical_reading import navigation_notes
                            from context_navigation import Navigation
                            from pipeline import received
                            target=root/'prepared-prefixes'/packet_sha
                            if 'page_navigation_mode' in status:
                                from page_navigation import deferred_prefix
                                partial=deferred_prefix(packet,status)
                            else:partial=Navigation(target,packet).build_prefix(navigation_notes(packet,status),client,received)
                            atomic(root/'prepared-prefix.json',dict(packet_sha256=packet_sha,through_date=row['through_date'],
                                complete=False,prefix=partial,shared_cache=str(client.navigation_cache_root.resolve())))
                    elif packet_bytes>DIRECT_BYTES:
                        target=root/'prepared-views'/packet_sha
                        view=reading_view(packet,target,client,lambda *args:None)
                        atomic(root/'prepared-navigation.json',dict(packet_sha256=packet_sha,through_date=row['through_date'],
                            navigation=view.get('context_navigation'),shared_cache=str(client.navigation_cache_root.resolve()),
                            limitation='仅记录日常局部导航；原回执和原文保留，可按完整来源重建，不是周评结果'))
                        # 日常树不被周评引用，完成后可从共享回执重建，避免每日复制整棵节点库。
                        # 未完成/unknown不进入这里，保留原执行目录供核账。
                        shutil.rmtree(target)
                    self.update(row['key'],'prepared','每日事实已准备；周期终判仍等待封口' if client.session_review else
                        '来源及必要阅读已准备；全局目标归并和评分仍等待封口',row['through_date'])
                except Paused as ex:
                    if row['mode']=='session_facts':
                        from review_protocol import failure
                        self.update(row['key'],failure(ex)['state'],str(ex))
                    else:self.update(row['key'],ex.state,str(ex))
                except Exception as ex:
                    import traceback
                    from execution_recovery import transient_connection
                    retryable=row['mode']=='session_facts' and transient_connection(ex,execution_phase)
                    atomic(root/'failure.json',dict(phase=execution_phase,through_date=row['through_date'],
                        error_type=type(ex).__name__,traceback=traceback.format_exc(),at=clock.time(),
                        category='transient_connection' if retryable else 'unclassified'))
                    self.update(row['key'],'failed' if row['mode']=='session_facts' else 'needs_review',
                        '每日事实执行失败，原文及回执保留；不属于员工审核' if row['mode']=='session_facts' else '日常来源或阅读合同未通过；保留记录待核')
                return True
        return False
