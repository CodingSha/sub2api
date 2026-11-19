-- 日评分使用独立合同，不将一天的数据伪装为自然周报告。
ALTER TABLE zhiyongli_reports DROP CONSTRAINT IF EXISTS zhiyongli_reports_period_type_check;
ALTER TABLE zhiyongli_reports ADD CONSTRAINT zhiyongli_reports_period_type_check CHECK (period_type IN ('day', 'week', 'month'));

ALTER TABLE zhiyongli_jobs DROP CONSTRAINT IF EXISTS zhiyongli_jobs_period_type_check;
ALTER TABLE zhiyongli_jobs ADD CONSTRAINT zhiyongli_jobs_period_type_check CHECK (period_type IN ('day', 'week', 'month'));
