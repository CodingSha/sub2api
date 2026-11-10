#!/usr/bin/env python3
"""只读审计一致性快照；缓存按内容寻址，完整扫描避免并发更新漏读。

数据库连接仅从环境 ZHIYONGLI_SOURCE_DSN 读取。此入口不识别任务、不评分。
"""
import argparse
from datetime import datetime, timezone
import hashlib
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import uuid
from personal_scope import excluded_user_ids

AUDIT_COLUMNS = ('id','request_id','session_id','request_count','user_id','api_key_id','platform',
    'endpoint','method','model','status_code','request_body','response_body','request_truncated',
    'response_truncated','created_at','updated_at')
QUERY_VERSION = 'per_user_nonempty_session_union_v1'


def require_current_query(snapshot):
    if snapshot.get('query_version') != QUERY_VERSION:
        raise ValueError('旧来源快照未绑定当前会话查询范围；保留原文及回执，核对后另建采集，不自动升级或续评分')


def save(path, value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return save_bytes(path, raw)


def save_bytes(path, raw):
    """原子写入已经序列化的正文，避免长记录重复生成同一份JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return hashlib.sha256(raw).hexdigest()


def file_sha256(path):
    """逐块验签，缓存命中及硬链接检查不把整条长正文再载入内存。"""
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def collect(connection, directory, excluded_keys, since, user_ids=None,shared_root=None):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    with (root / 'source.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('已有来源采集，禁止并发覆盖快照') from None
        try:
            return _collect(connection, directory, excluded_keys, since, user_ids,shared_root)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class ObjectCache:
    """完整元数据对账，正文仅取新修订；硬链接共享原文，不依赖跨快照游标。"""
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(self.root,0o700)
        # 首次切换WAL可能直接返回SQLITE_BUSY；按共享目录串行初始化，不依赖忙等待或重试。
        with os.fdopen(os.open(self.root/'source-index-init.lock',os.O_RDWR|os.O_CREAT,0o600),'a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            self.db=sqlite3.connect(self.root/'source-index.sqlite',isolation_level=None)
            try:
                self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
                self.db.execute('CREATE TABLE IF NOT EXISTS revisions(revision TEXT PRIMARY KEY,object_sha TEXT NOT NULL)')
                os.chmod(self.root/'source-index.sqlite',0o600)
            except BaseException:
                self.db.close()
                raise

    def get(self,revision):
        row=self.db.execute('SELECT object_sha FROM revisions WHERE revision=?',(revision,)).fetchone()
        if not row:return None
        sha=row[0];path=self.root/'objects'/(sha+'.json')
        if not path.exists() or file_sha256(path)!=sha:
            raise ValueError('共享来源缓存缺失或签名损坏，须核对，不能悄悄重抓覆盖')
        return sha

    def put(self,revision,value):
        raw=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        sha=hashlib.sha256(raw).hexdigest();path=self.root/'objects'/(sha+'.json')
        if path.exists():
            if file_sha256(path)!=sha:raise ValueError('共享原文签名损坏')
        else:save_bytes(path,raw)
        self.db.execute('INSERT OR IGNORE INTO revisions VALUES(?,?)',(revision,sha))
        if self.get(revision)!=sha:raise ValueError('同来源修订映射发生冲突')
        return sha

    def attach(self,sha,root):
        source=self.root/'objects'/(sha+'.json');target=Path(root)/'objects'/(sha+'.json')
        target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if not target.exists():
            try:os.link(source,target)
            except FileExistsError:pass
            except OSError as ex:raise ValueError('共享原文与任务目录须在同一文件系统，禁止隐式复制全量正文') from ex
        if file_sha256(target)!=sha:raise ValueError('任务原文引用签名损坏')


def metadata_batches(connection, people, start, excluded_keys, revision):
    """每人只定位一次来源；有效会话前文完整保留，服务端游标逐批读取签名。"""
    query = """WITH recent AS MATERIALIZED (
        SELECT id,user_id,session_id,updated_at,api_key_id,request_truncated,response_truncated
        FROM llm_audit_logs WHERE user_id=%s AND updated_at >= %s
    ), sessions AS MATERIALIZED (
        SELECT DISTINCT user_id,session_id FROM recent WHERE session_id IS NOT NULL AND session_id<>''
    ), candidates AS (
        SELECT id,updated_at,api_key_id,request_truncated,response_truncated FROM recent UNION
        SELECT h.id,h.updated_at,h.api_key_id,h.request_truncated,h.response_truncated
        FROM sessions s JOIN llm_audit_logs h ON h.user_id=s.user_id AND h.session_id=s.session_id
    ) SELECT id,updated_at,request_truncated,response_truncated FROM candidates
        WHERE api_key_id IS NULL OR NOT(api_key_id=ANY(%s::bigint[]))
        ORDER BY updated_at,id"""
    for person_id, _ in people:
        # 只读事务内的服务端游标延续同一查询，避免每250条重算全部活跃会话。
        with connection.cursor(name='zhiyongli_metadata_'+uuid.uuid4().hex) as metadata:
            metadata.execute(query, (person_id, start, excluded_keys))
            while True:
                batch = metadata.fetchmany(250)
                if not batch:
                    break
                # 正文签名仅按本批主键计算，避免最终连接/排序先处理全表或全部长正文。
                ids = [row[0] for row in batch]
                revisions = connection.execute('SELECT l.id,'+revision+
                    ' FROM llm_audit_logs l WHERE l.id=ANY(%s::bigint[])', (ids,)).fetchall()
                hashes = dict(revisions)
                if len(hashes) != len(revisions) or set(hashes) != set(ids):
                    raise ValueError('同一稳定快照的修订签名缺失或重复')
                yield [(*row, hashes[row[0]]) for row in batch]


def _collect(connection, directory, excluded_keys, since, user_ids=None,shared_root=None):
    """单事务稳定视图内按人定位及流式分页；不跨事务冒充可靠增量。"""
    start = datetime.fromisoformat(since.replace('Z', '+00:00'))
    if start.tzinfo is None:
        raise ValueError('来源起点必须包含时区')
    root = Path(directory)
    rows = []
    excluded_people = excluded_user_ids()
    if user_ids is not None and (not user_ids or any(type(i) is not int or i < 1 for i in user_ids)):
        raise ValueError('来源人员范围无效')
    cache=ObjectCache(shared_root or root);fetched=reused=0
    try:
        with connection.transaction():
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            stamp = connection.execute('SELECT transaction_timestamp()').fetchone()[0].isoformat()
            people = connection.execute('''SELECT id, email FROM users
                WHERE deleted_at IS NULL AND id NOT IN (SELECT user_id FROM audit_whitelist_users)
                AND NOT (id=ANY(%s::bigint[]))
                AND (%s::bigint[] IS NULL OR id=ANY(%s::bigint[])) ORDER BY id''', (excluded_people, user_ids, user_ids)).fetchall()
            projection='jsonb_build_object('+','.join("'"+c+"',l."+c for c in AUDIT_COLUMNS)+')'
            revision="encode(sha256(convert_to(("+projection+")::text,'UTF8')),'hex')"
            for batch in metadata_batches(connection, people, start, excluded_keys, revision):
                cached={row[0]:cache.get(row[4]) for row in batch}
                missing=[row[0] for row in batch if cached[row[0]] is None]
                if missing:
                    revisions={row[0]:row[4] for row in batch};seen=set()
                    # 正文可能很长；服务端游标每次只送一条，不能把250条正文同时留在内存。
                    with connection.cursor(name='zhiyongli_source_'+uuid.uuid4().hex) as bodies:
                        bodies.itersize=1
                        bodies.execute('SELECT l.id,'+projection+' FROM llm_audit_logs l WHERE l.id=ANY(%s::bigint[])',(missing,))
                        for audit_id,value in bodies:
                            if audit_id not in missing or audit_id in seen:raise ValueError('来源正文重复或越界')
                            cached[audit_id]=cache.put(revisions[audit_id],{key:value[key] for key in AUDIT_COLUMNS})
                            seen.add(audit_id);fetched+=1
                    if seen!=set(missing):raise ValueError('同一稳定快照的修订正文缺失')
                for audit_id, updated, request_truncated, response_truncated, revision_hash in batch:
                    sig=cached[audit_id]
                    if audit_id not in missing:reused+=1
                    rel = Path('objects') / (sig + '.json')
                    cache.attach(sig,root)
                    rows.append(dict(id=audit_id, updated_at=updated.isoformat(), path=str(rel), sha256=sig,
                                     truncated=bool(request_truncated or response_truncated)))
    finally:cache.db.close()
    rows.sort(key=lambda row: (datetime.fromisoformat(row['updated_at']), row['id']))
    result = dict(version=1, snapshot_at=stamp, mode='repeatable_read_metadata_reconciled', since=start.isoformat(),
                  cursor_scope='snapshot_only', query_version=QUERY_VERSION, excluded_api_key_ids=excluded_keys,
                  excluded_user_ids=excluded_people,
                  users=[dict(id=p[0], email=p[1]) for p in people], rows=rows,
                  truncated_rows=sum(row['truncated'] for row in rows),
                  limitation='审计截断、未采集及用户角色不证明本人创作；任务识别必须另行核验完整过程。')
    snapshot_id = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    snapshot_sha = save(root / 'snapshots' / (snapshot_id + '.json'), result)
    save(root / 'latest.json', dict(snapshot=snapshot_id, path='snapshots/' + snapshot_id + '.json', sha256=snapshot_sha,
                                  completed_at=datetime.now(timezone.utc).isoformat()))
    return dict(snapshot=snapshot_id, rows=len(rows), users=len(people), truncated_rows=result['truncated_rows'],
        fetched_body_rows=fetched,reused_body_rows=reused)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True)
    parser.add_argument('--since', required=True, help='本期开始时刻，RFC3339含时区；活跃会话保留完整前文')
    parser.add_argument('--exclude-api-key-id', type=int, action='append', required=True,
                        help='至少明确排除一个评审专用Key，防止评审流量回流')
    args = parser.parse_args()
    import psycopg
    dsn = os.environ.get('ZHIYONGLI_SOURCE_DSN')
    if not dsn:
        parser.error('缺少 ZHIYONGLI_SOURCE_DSN')
    with psycopg.connect(dsn) as connection:
        print(json.dumps(collect(connection, args.state, sorted(set(args.exclude_api_key_id)), args.since)))


if __name__ == '__main__':
    main()
