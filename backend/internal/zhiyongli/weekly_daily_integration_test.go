//go:build integration

package zhiyongli

import (
	"context"
	"fmt"
	"testing"

	"github.com/Wei-Shaw/sub2api/migrations"
)

func TestWeeklyDailyPublishedSourcesPrivacyAndChanges(t *testing.T) {
	s := testStore(t)
	raw, err := migrations.FS.ReadFile("248_zhiyongli_daily_reports.sql")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.db.Exec(string(raw)); err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	w := weeklyDailyFixture(t)
	w.UserEmail = "person@example.test"
	ids := map[string]int64{}
	for i, day := range w.WorkdayScope.WorkingDates {
		d := dailyFixture()
		d.UserEmail = w.UserEmail
		if day != "2026-11-18" {
			d.PeriodStart = day
			d.WorkdayScope, _ = dailyWindow(day)
			d.PeriodEnd = d.WorkdayScope.PeriodEnd
			d.DailyReview.WindowStart = day + "T08:00:00+08:00"
			d.DailyReview.WindowEnd = day + "T18:00:00+08:00"
			d.DailyReview.Conversations = map[string]DailyTask{}
			d.DailyReview.Behaviors = map[string]DailyBehavior{}
			d.DailyReview.SourceLabels = map[string]string{}
			d.DailyReview.SourceSessions = map[string]string{}
			d.Items = nil
			d.ScoreStatus, d.ScoreReason = "no_activity", "当日没有可评分记录。"
		}
		run, err := s.Import(ctx, Bundle{SchemaVersion: 1, SourceName: fmt.Sprintf("day-%d", i), Reports: []Result{d}}, 1)
		if err != nil {
			t.Fatal(err)
		}
		var id int64
		if err = s.db.QueryRow(`SELECT id FROM zhiyongli_reports WHERE run_id=$1`, run.ID).Scan(&id); err != nil {
			t.Fatal(err)
		}
		if _, err = s.Publish(ctx, id, 1, 1); err != nil {
			t.Fatal(err)
		}
		w.WeeklyReview.Sources[i].ReportID = id
		ids[day] = id
	}
	bundle := Bundle{SchemaVersion: 1, SourceName: "weekly-from-days", Reports: []Result{w}}
	run, err := s.Import(ctx, bundle, 1)
	if err != nil {
		t.Fatal(err)
	}
	rows, total, err := s.List(ctx, Filter{PeriodType: "week", Page: 1, PageSize: 20})
	if err != nil || total != 1 || rows[0].WeeklyReview != nil || rows[0].Items != nil {
		t.Fatal("列表不能泄露周评原文", err)
	}
	id := rows[0].ID
	if _, err = s.Get(ctx, id, 2); err == nil {
		t.Fatal("本人不能读取草稿")
	}
	if _, err = s.Publish(ctx, id, 1, 1); err != nil {
		t.Fatal(err)
	}
	got, err := s.Get(ctx, id, 2)
	if err != nil || got.WeeklyReview == nil || *got.Score != "82.0" {
		t.Fatal("本人应能读到已发布周评", err)
	}
	if _, err = s.Get(ctx, id, 3); err == nil {
		t.Fatal("不能读取他人周评原文")
	}
	again, err := s.Import(ctx, bundle, 1)
	if err != nil || again.ID != run.ID {
		t.Fatal("同包恢复须幂等", err)
	}
	// 日评后续修订替换后，旧来源周评不能再导入或发布。
	if _, err = s.db.Exec(`UPDATE zhiyongli_reports SET publication_status='superseded' WHERE id=$1`, ids["2026-11-18"]); err != nil {
		t.Fatal(err)
	}
	bundle.SourceName = "stale-week-source"
	if _, err = s.Import(ctx, bundle, 1); err == nil {
		t.Fatal("应拒绝来源已变的周评")
	}
	if _, err = s.Publish(ctx, id, 1, 1); err == nil {
		t.Fatal("发布检查必须拦截已变的日评来源")
	}
	// 只修改周评原文即使同步修改正文哈希也不能绕过来源比对。
	if _, err = s.db.Exec(`UPDATE zhiyongli_reports SET publication_status='published' WHERE id=$1`, ids["2026-11-18"]); err != nil {
		t.Fatal(err)
	}
	w = weeklyDailyFixture(t)
	w.UserEmail = "person@example.test"
	for i, day := range w.WorkdayScope.WorkingDates {
		w.WeeklyReview.Sources[i].ReportID = ids[day]
	}
	task := w.WeeklyReview.Conversations["d20261118-task-1"]
	task.Goal = "未经日评确认的任务目标"
	w.WeeklyReview.Conversations["d20261118-task-1"] = task
	if _, err = s.Import(ctx, Bundle{SchemaVersion: 1, SourceName: "tampered-task", Reports: []Result{w}}, 1); err == nil {
		t.Fatal("周评不能篡改已核任务")
	}
}
