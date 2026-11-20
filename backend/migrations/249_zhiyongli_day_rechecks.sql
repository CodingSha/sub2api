ALTER TABLE zhiyongli_jobs
    ADD COLUMN recheck_report_id BIGINT REFERENCES zhiyongli_reports(id),
    ADD COLUMN recheck_run_id BIGINT REFERENCES zhiyongli_runs(id),
    ADD CONSTRAINT zhiyongli_jobs_recheck_scope CHECK (
        (recheck_report_id IS NOT NULL OR recheck_run_id IS NULL)
        AND (recheck_report_id IS NULL OR period_type = 'day')
    );

CREATE UNIQUE INDEX zhiyongli_jobs_active_day_recheck
    ON zhiyongli_jobs(user_id,period_start)
    WHERE recheck_report_id IS NOT NULL AND state IN ('queued','running');

CREATE UNIQUE INDEX zhiyongli_jobs_recheck_run
    ON zhiyongli_jobs(recheck_run_id) WHERE recheck_run_id IS NOT NULL;
