-- Final compatibility pass for installations upgraded from either audit implementation.
DO $$
BEGIN
    IF to_regclass('public.audit_logs') IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = 'audit_logs' AND column_name = 'request_truncated'
       )
       AND NOT EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = 'audit_logs' AND column_name = 'actor_user_id'
       ) THEN
        IF to_regclass('public.llm_audit_logs') IS NOT NULL THEN
            RAISE EXCEPTION 'cannot migrate legacy audit_logs: llm_audit_logs already exists';
        END IF;
        ALTER TABLE audit_logs RENAME TO llm_audit_logs;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS audit_logs (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor_user_id BIGINT,
    actor_email VARCHAR(255) NOT NULL DEFAULT '',
    actor_role VARCHAR(32) NOT NULL DEFAULT '',
    auth_method VARCHAR(32) NOT NULL DEFAULT '',
    credential_masked VARCHAR(160) NOT NULL DEFAULT '',
    action VARCHAR(128) NOT NULL DEFAULT '',
    method VARCHAR(16) NOT NULL DEFAULT '',
    path VARCHAR(512) NOT NULL DEFAULT '',
    request_id VARCHAR(64) NOT NULL DEFAULT '',
    client_ip VARCHAR(64) NOT NULL DEFAULT '',
    user_agent VARCHAR(512) NOT NULL DEFAULT '',
    request_body TEXT NOT NULL DEFAULT '',
    status_code INT NOT NULL DEFAULT 0,
    latency_ms BIGINT NOT NULL DEFAULT 0,
    extra JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at_id
    ON audit_logs (created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_actor_created
    ON audit_logs (actor_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_action
    ON audit_logs (action);
CREATE INDEX IF NOT EXISTS idx_audit_logs_client_ip
    ON audit_logs (client_ip);

CREATE INDEX IF NOT EXISTS idx_llm_audit_logs_created_at
    ON llm_audit_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_audit_logs_request_id
    ON llm_audit_logs (request_id);
CREATE INDEX IF NOT EXISTS idx_llm_audit_logs_user_id_created_at
    ON llm_audit_logs (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_audit_logs_api_key_id_created_at
    ON llm_audit_logs (api_key_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_audit_logs_platform_created_at
    ON llm_audit_logs (platform, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_audit_logs_updated_at
    ON llm_audit_logs (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_audit_logs_session_id
    ON llm_audit_logs (session_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_llm_audit_logs_session_scope_uniq
    ON llm_audit_logs (session_scope) WHERE session_scope <> '';
