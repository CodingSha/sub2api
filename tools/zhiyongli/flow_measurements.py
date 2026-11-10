"""请求遥测只存阶段、终态、耗时与用量，不保存正文或冒充评分验收。"""
import math


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS measurements(
        attempt_id TEXT PRIMARY KEY,request_kind TEXT NOT NULL,terminal_state TEXT NOT NULL,
        client_seconds REAL,input_tokens INTEGER,output_tokens INTEGER,total_tokens INTEGER)''')


def tokens(value):
    return value if type(value) is int and value>=0 else None


def save(db,attempt_id,record):
    elapsed=record.get('client_elapsed_seconds')
    if type(elapsed) not in (int,float) or not math.isfinite(elapsed) or elapsed<0:elapsed=None
    kind=record.get('request_kind')
    if (not isinstance(kind,str) or not kind or len(kind)>80
        or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_' for c in kind)):
        kind='unclassified'
    response=record.get('response')
    if not isinstance(response,dict):response={}
    usage=response.get('usage')
    if not isinstance(usage,dict):usage={}
    terminal=record.get('status')
    if terminal=='received':
        terminal=response.get('status')
        if terminal not in {'completed','incomplete','failed'}:terminal='received_unclassified'
    elif terminal not in {'rejected','unknown','cancelled'}:terminal='unknown'
    db.execute('INSERT OR IGNORE INTO measurements VALUES(?,?,?,?,?,?,?)',
        (attempt_id,kind,terminal,elapsed,tokens(usage.get('input_tokens')),
         tokens(usage.get('output_tokens')),tokens(usage.get('total_tokens'))))


def snapshot(db,budget_key):
    terminal=db.execute("SELECT count(*) FROM attempts WHERE budget_key=? AND status NOT IN ('active','cancelled')",(budget_key,)).fetchone()[0]
    available=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='measurements'").fetchone() is not None
    stages=[]
    if available:
        stages=[dict(r) for r in db.execute('''SELECT m.request_kind,m.terminal_state,count(*) AS requests,
            count(m.client_seconds) AS measured_latency_requests,min(m.client_seconds) AS minimum_client_seconds,
            avg(m.client_seconds) AS mean_client_seconds,max(m.client_seconds) AS maximum_client_seconds,
            count(CASE WHEN m.input_tokens IS NOT NULL AND m.output_tokens IS NOT NULL THEN 1 END) AS measured_input_output_requests,
            sum(m.input_tokens) AS input_tokens,sum(m.output_tokens) AS output_tokens,sum(m.total_tokens) AS total_tokens
            FROM measurements m JOIN attempts a ON a.id=m.attempt_id
            WHERE a.budget_key=? AND a.status NOT IN ('active','cancelled')
            GROUP BY m.request_kind,m.terminal_state ORDER BY m.request_kind,m.terminal_state''',(budget_key,))]
    return dict(measurement_table_available=available,terminal_requests=terminal,
        measured_latency_requests=sum(r['measured_latency_requests'] for r in stages),
        measured_input_output_requests=sum(r['measured_input_output_requests'] for r in stages),stages=stages,
        scope='current_budget_key',latency_unit='seconds',
        latency_meaning='客户端从发起HTTP到收到终态/异常的单调时钟耗时，含传输和本地流处理；不是纯模型生成时间或重启核账耗时。',
        limitation='缺失保持unknown；完整/截断/失败分列，不证明真实模型配额、评分语义、容量或代表性。')
