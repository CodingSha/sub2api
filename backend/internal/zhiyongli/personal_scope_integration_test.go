//go:build integration

package zhiyongli

import (
	"context"
	"testing"
	"time"
)

func TestPersonalExclusionGuardsManualAutomaticImportAndPublish(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	if _, err := s.db.Exec("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'; UPDATE users SET role='admin' WHERE id=1"); err != nil {
		t.Fatal(err)
	}
	// 先保留历史候选，排除后拒绝发布及恢复，且不删除既有记录。
	b := Bundle{SchemaVersion: 1, SourceName: "synthetic-personal-scope", Reports: []Result{sampleResult()}}
	if _, err := s.Import(ctx, b, 1); err != nil {
		t.Fatal(err)
	}
	reports, _, _ := s.List(ctx, Filter{Page: 1, PageSize: 20})
	t.Setenv("ZHIYONGLI_EXCLUDED_USER_IDS", "2")
	if _, err := s.Publish(ctx, reports[0].ID, 1, 1); err == nil {
		t.Fatal("公共账户候选被发布")
	}
	b.SourceName += "-new"
	if _, err := s.Import(ctx, b, 1); err == nil {
		t.Fatal("公共账户新报告被导入")
	}
	now := time.Date(2026, 9, 12, 3, 0, 0, 0, time.FixedZone("CST", 8*3600))
	if _, err := s.startJobsAt(ctx, StartJobs{UserIDs: []int64{1, 2}, PeriodType: "week", PeriodStart: "2026-09-07"}, 1, now); err == nil {
		t.Fatal("手动混入排除账户未被拒绝")
	}
	c := ScheduleConfig{Version: 1, Enabled: true, FirstWeek: "2026-09-07", ActorID: 1}
	daily, err := s.ScheduleDue(ctx, c, now.AddDate(0, 0, -1))
	if err != nil || daily.Preparation == nil || len(daily.Preparation.Users) != 2 {
		t.Fatal("日常准备排除失败", err)
	}
	closed, err := s.ScheduleDue(ctx, c, now)
	if err != nil || len(closed.Jobs) != 2 {
		t.Fatal("自动入队排除失败", err)
	}
	var audits, excludedJobs, retained int
	s.db.QueryRow("SELECT count(*) FROM audit_whitelist_users").Scan(&audits)
	s.db.QueryRow("SELECT count(*) FROM zhiyongli_jobs WHERE user_id=2").Scan(&excludedJobs)
	s.db.QueryRow("SELECT count(*) FROM zhiyongli_reports WHERE user_id=2 AND publication_status='draft'").Scan(&retained)
	if audits != 0 || excludedJobs != 0 || retained != 1 {
		t.Fatal("个人范围改变了采集白名单、创建排除任务或删除历史")
	}
}

func TestExcludedHistoricalJobCannotResume(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	jobs, err := s.startJobsAt(ctx, StartJobs{UserIDs: []int64{2}, PeriodType: "week", PeriodStart: "2026-09-07"}, 1,
		time.Date(2026, 9, 12, 3, 0, 0, 0, time.FixedZone("CST", 8*3600)))
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.db.Exec("UPDATE zhiyongli_jobs SET state='needs_review' WHERE id=$1", jobs[0].ID); err != nil {
		t.Fatal(err)
	}
	t.Setenv("ZHIYONGLI_EXCLUDED_USER_IDS", "2")
	if _, err = s.ResumeJob(ctx, jobs[0].ID); err == nil {
		t.Fatal("排除后的历史任务被恢复")
	}
	var state string
	if err = s.db.QueryRow("SELECT state FROM zhiyongli_jobs WHERE id=$1", jobs[0].ID).Scan(&state); err != nil || state != "needs_review" {
		t.Fatal("历史任务状态被改写", err)
	}
}
