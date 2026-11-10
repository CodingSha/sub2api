-- 管理员启动的持久评审任务；原始来源仍只读，自动发布默认不接入。
CREATE TABLE IF NOT EXISTS zhiyongli_jobs (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    period_type TEXT NOT NULL CHECK (period_type IN ('week','month')),
    period_start DATE NOT NULL,
    period_end DATE NOT NULL CHECK (period_end > period_start),
    rules_version TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued','running','needs_review','unknown','failed','completed','no_activity')),
    phase TEXT NOT NULL DEFAULT 'collecting',
    completed_units INTEGER NOT NULL DEFAULT 0 CHECK (completed_units >= 0),
    total_units INTEGER NOT NULL DEFAULT 0 CHECK (total_units >= completed_units),
    message TEXT NOT NULL DEFAULT '',
    created_by BIGINT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id,period_type,period_start,rules_version)
);
CREATE INDEX IF NOT EXISTS zhiyongli_jobs_queue ON zhiyongli_jobs(state,updated_at,id);
