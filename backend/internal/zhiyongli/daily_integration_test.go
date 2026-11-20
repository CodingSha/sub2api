//go:build integration

package zhiyongli

import (
	"context"
	"testing"
	"time"

	"github.com/Wei-Shaw/sub2api/migrations"
)

func TestDailyScheduleCutoffCompensationExclusionsAndIdempotency(t *testing.T) {
	s := testStore(t)
	raw, err := migrations.FS.ReadFile("248_zhiyongli_daily_reports.sql")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.db.Exec(string(raw)); err != nil {
		t.Fatal(err)
	}
	if _, err = s.db.Exec(`ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'; UPDATE users SET role='admin' WHERE id=1; INSERT INTO users(id,email) VALUES(22,'automation@example.test'); INSERT INTO audit_whitelist_users(user_id) VALUES(1)`); err != nil {
		t.Fatal(err)
	}
	t.Setenv("ZHIYONGLI_EXCLUDED_USER_IDS", "22")
	c := ScheduleConfig{Version: 2, Enabled: true, FirstDay: "2026-11-18", ActorID: 1}
	ctx := context.Background()
	before, _ := time.Parse(time.RFC3339, "2026-11-18T22:59:59+08:00")
	result, err := s.ScheduleDue(ctx, c, before)
	if err != nil || len(result.Jobs) != 0 {
		t.Fatal("23点之前不应创建当天评分任务", err)
	}
	closed := before.Add(time.Second)
	result, err = s.ScheduleDue(ctx, c, closed)
	if err != nil || len(result.Jobs) != 2 || result.Preparation != nil || result.FirstDay != c.FirstDay {
		t.Fatal("已封口日应只为参评人员排队", err)
	}
	ids := map[int64]bool{}
	for _, job := range result.Jobs {
		if job.PeriodType != "day" || job.PeriodStart != c.FirstDay || job.PeriodEnd != "2026-11-19" || job.WorkdayScope.Version != DailyReviewVersion || job.UserID == 22 || job.UserID == 1 {
			t.Fatalf("日评范围或排除名单失效: %+v", job)
		}
		ids[job.ID] = true
	}
	morning, _ := time.Parse(time.RFC3339, "2026-11-19T07:59:59+08:00")
	again, err := s.ScheduleDue(ctx, c, morning)
	if err != nil || len(again.Jobs) != 2 {
		t.Fatal("跨午夜补偿缺失", err)
	}
	for _, job := range again.Jobs {
		if !ids[job.ID] {
			t.Fatal("重复调度创建了新任务")
		}
	}
	if _, err = s.ScheduleDue(ctx, ScheduleConfig{Version: 2, Enabled: true, FirstDay: c.FirstDay, FirstWeek: "2026-11-16", ActorID: 1}, morning); err == nil {
		t.Fatal("新旧计划不能混合配置")
	}
}

func TestDailyImportPublishPrivacyAndIdempotency(t *testing.T) {
	s := testStore(t)
	raw, err := migrations.FS.ReadFile("248_zhiyongli_daily_reports.sql")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.db.Exec(string(raw)); err != nil {
		t.Fatal(err)
	}
	r := dailyFixture()
	r.UserEmail = "person@example.test"
	bundle := Bundle{SchemaVersion: 1, SourceName: "daily-local-acceptance", Reports: []Result{r}}
	ctx := context.Background()
	run, err := s.Import(ctx, bundle, 1)
	if err != nil {
		t.Fatal(err)
	}
	again, err := s.Import(ctx, bundle, 1)
	if err != nil || again.ID != run.ID {
		t.Fatal("同包重发不应创建新修订", err)
	}
	rows, total, err := s.List(ctx, Filter{PeriodType: "day", Page: 1, PageSize: 20})
	if err != nil || total != 1 || len(rows) != 1 || rows[0].DailyReview != nil || rows[0].Items != nil {
		t.Fatal("日评分列表不应返回对话正文", err)
	}
	id := rows[0].ID
	if _, err = s.Get(ctx, id, 2); err == nil {
		t.Fatal("未发布日评不得向本人开放")
	}
	published, err := s.Publish(ctx, id, rows[0].Revision, 1)
	if err != nil {
		t.Fatal(err)
	}
	if published.Score == nil || *published.Score != "82.0" {
		t.Fatal("发布日分不一致")
	}
	detail, err := s.Get(ctx, id, 2)
	if err != nil || detail.DailyReview == nil || len(detail.Items[0].Evidence) != 1 {
		t.Fatal("本人已发布日分应有实际上下文", err)
	}
	if _, err = s.Get(ctx, id, 3); err == nil {
		t.Fatal("不能读取他人日分")
	}
	// 第二日独立保存，日期筛选须保持第一日可见。
	r.PeriodStart = "2026-11-17"
	r.WorkdayScope, _ = dailyWindow(r.PeriodStart)
	r.PeriodEnd = "2026-11-18"
	r.DailyReview.WindowStart = "2026-11-17T08:00:00+08:00"
	r.DailyReview.WindowEnd = "2026-11-17T18:00:00+08:00"
	for i := range r.Items {
		for j := range r.Items[i].Evidence {
			r.Items[i].Evidence[j].OccurredAt = r.Items[i].Evidence[j].OccurredAt.AddDate(0, 0, -1)
		}
	}
	if _, err = s.Import(ctx, Bundle{SchemaVersion: 1, SourceName: "previous-day", Reports: []Result{r}}, 1); err != nil {
		t.Fatal(err)
	}
	rows, total, err = s.List(ctx, Filter{UserID: 2, PeriodType: "day", PeriodStart: "2026-11-18", Status: "published", Page: 1, PageSize: 20})
	if err != nil || total != 1 || len(rows) != 1 || rows[0].ID != id {
		t.Fatal("按日筛选返回错误报告", err)
	}
	// 模拟导入后、发布前中断：同任务接续须发布原Run，不重新评分或生成修订。
	jobs, err := s.startJobsAt(ctx, StartJobs{UserIDs: []int64{2}, PeriodType: "day", PeriodStart: r.PeriodStart}, 1, time.Now())
	if err != nil || len(jobs) != 1 {
		t.Fatal("创建日评任务失败", err)
	}
	if _, err = s.db.Exec(`UPDATE zhiyongli_jobs SET state='running' WHERE id=$1`, jobs[0].ID); err != nil {
		t.Fatal(err)
	}
	dayBundle := Bundle{SchemaVersion: 1, SourceName: "previous-day", Reports: []Result{r}}
	draftRun, err := s.ImportDayJobDraft(ctx, jobs[0].ID, dayBundle)
	if err != nil {
		t.Fatal("限定日评试评应仅保存草稿", err)
	}
	draftAgain, err := s.ImportDayJobDraft(ctx, jobs[0].ID, dayBundle)
	if err != nil || draftAgain.ID != draftRun.ID {
		t.Fatal("试评草稿接续应幂等", err)
	}
	rows, total, err = s.List(ctx, Filter{PeriodType: "day", PeriodStart: r.PeriodStart, Status: "draft", Page: 1, PageSize: 20})
	if err != nil || total != 1 || len(rows) != 1 {
		t.Fatal("管理员应能核对限定试评草稿", err)
	}
	if _, err = s.Get(ctx, rows[0].ID, 2); err == nil {
		t.Fatal("限定试评不得自动向本人开放")
	}
	if _, visible, e := s.List(ctx, Filter{UserID: 2, PeriodType: "day", PeriodStart: r.PeriodStart, Page: 1, PageSize: 20}); e != nil || visible != 0 {
		t.Fatal("限定试评草稿不得出现在员工日评列表", e)
	}
	autoRun, err := s.ImportJob(ctx, jobs[0].ID, dayBundle)
	if err != nil || autoRun.ID != draftRun.ID {
		t.Fatal("日评应自动发布", err)
	}
	if _, err = s.ImportDayJobDraft(ctx, jobs[0].ID, dayBundle); err != ErrConflict {
		t.Fatal("已发布报告不得被试评端点当成草稿返回", err)
	}
	sameRun, err := s.ImportJob(ctx, jobs[0].ID, dayBundle)
	if err != nil || autoRun.ID != sameRun.ID {
		t.Fatal("自动发布接续应幂等", err)
	}
	_, total, err = s.List(ctx, Filter{UserID: 2, PeriodType: "day", PeriodStart: r.PeriodStart, Status: "published", Page: 1, PageSize: 20})
	if err != nil || total != 1 {
		t.Fatal("接续后本人应可见当天评分", err)
	}
}
