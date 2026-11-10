-- 智用力报告只保存冻结结果；重新评审产生新修订，发布时保留旧版。
CREATE TABLE IF NOT EXISTS zhiyongli_runs (
    id BIGSERIAL PRIMARY KEY,
    digest TEXT NOT NULL UNIQUE,
    source_name TEXT NOT NULL,
    report_count INTEGER NOT NULL CHECK (report_count > 0),
    created_by BIGINT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS zhiyongli_reports (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES zhiyongli_runs(id),
    user_id BIGINT NOT NULL REFERENCES users(id),
    period_type TEXT NOT NULL CHECK (period_type IN ('week', 'month')),
    period_start DATE NOT NULL,
    period_end DATE NOT NULL CHECK (period_end > period_start),
    revision INTEGER NOT NULL CHECK (revision > 0),
    publication_status TEXT NOT NULL DEFAULT 'draft' CHECK (publication_status IN ('draft', 'published', 'superseded')),
    body JSONB NOT NULL,
    published_by BIGINT REFERENCES users(id),
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, period_type, period_start, revision)
);
CREATE UNIQUE INDEX IF NOT EXISTS zhiyongli_current_publication
    ON zhiyongli_reports(user_id, period_type, period_start) WHERE publication_status = 'published';
CREATE INDEX IF NOT EXISTS zhiyongli_user_period ON zhiyongli_reports(user_id, period_type, period_start DESC, id DESC);
CREATE INDEX IF NOT EXISTS zhiyongli_report_period ON zhiyongli_reports(period_type, period_start DESC, id DESC);

CREATE TABLE IF NOT EXISTS zhiyongli_evidence (
    report_id BIGINT NOT NULL REFERENCES zhiyongli_reports(id) ON DELETE CASCADE,
    item_code TEXT NOT NULL,
    evidence_key TEXT NOT NULL,
    body JSONB NOT NULL,
    PRIMARY KEY (report_id, item_code, evidence_key)
);
