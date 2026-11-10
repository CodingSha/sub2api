-- 新任务显式冻结工作日范围；历史自然周任务保留，不自动升级。
ALTER TABLE zhiyongli_jobs ADD COLUMN IF NOT EXISTS scope_key TEXT NOT NULL DEFAULT 'legacy-natural-week';
ALTER TABLE zhiyongli_jobs ADD COLUMN IF NOT EXISTS workday_scope JSONB;
DO $$
DECLARE c RECORD;
BEGIN
    FOR c IN SELECT conname FROM pg_constraint
        WHERE conrelid='zhiyongli_jobs'::regclass AND contype='u'
        AND pg_get_constraintdef(oid)='UNIQUE (user_id, period_type, period_start, rules_version)'
    LOOP
        EXECUTE format('ALTER TABLE zhiyongli_jobs DROP CONSTRAINT %I',c.conname);
    END LOOP;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS zhiyongli_jobs_scope_unique
    ON zhiyongli_jobs(user_id,period_type,period_start,rules_version,scope_key);
