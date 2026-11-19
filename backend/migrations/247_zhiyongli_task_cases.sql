-- 案例记录按报告修订隔离；摘要保留计数与分析，原文仅按需并经权限检查读取。
CREATE TABLE IF NOT EXISTS zhiyongli_task_cases (
    report_id BIGINT NOT NULL REFERENCES zhiyongli_reports(id) ON DELETE CASCADE,
    case_key TEXT NOT NULL,
    body JSONB NOT NULL,
    PRIMARY KEY (report_id, case_key)
);
