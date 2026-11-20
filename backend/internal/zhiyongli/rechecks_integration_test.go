//go:build integration

package zhiyongli

import (
	"context"
	"errors"
	"sync"
	"testing"

	"github.com/Wei-Shaw/sub2api/migrations"
)

func TestDayRecheckQueuesOnceAndKeepsPublishedReportUntilExplicitPublish(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	daily, err := migrations.FS.ReadFile("248_zhiyongli_daily_reports.sql")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.db.Exec(string(daily)); err != nil {
		t.Fatal(err)
	}
	result := dailyFixture()
	result.UserEmail = "person@example.test"
	bundle := Bundle{SchemaVersion: 1, SourceName: "day-recheck-test", Reports: []Result{result}}
	jobs, err := s.StartJobs(ctx, StartJobs{UserIDs: []int64{2}, PeriodType: "day", PeriodStart: result.PeriodStart}, 1)
	if err != nil || len(jobs) != 1 {
		t.Fatal("原日评未登记", err)
	}
	markState := func(id int64, state string) {
		t.Helper()
		if _, err := s.db.Exec(`UPDATE zhiyongli_jobs SET state=$1 WHERE id=$2`, state, id); err != nil {
			t.Fatal(err)
		}
	}
	markState(jobs[0].ID, "running")
	originalRun, err := s.ImportJob(ctx, jobs[0].ID, bundle)
	if err != nil {
		t.Fatal("普通日评仍应直接发布", err)
	}
	markState(jobs[0].ID, "completed")
	var originalID int64
	if err = s.db.QueryRow(`SELECT id FROM zhiyongli_reports WHERE run_id=$1`, originalRun.ID).Scan(&originalID); err != nil {
		t.Fatal(err)
	}
	if _, err = s.StartDayRecheck(ctx, 99999, 1); !errors.Is(err, ErrNotFound) {
		t.Fatal("不存在的报告不能复评", err)
	}
	var callers sync.WaitGroup
	queued := make(chan Job, 6)
	for range cap(queued) {
		callers.Add(1)
		go func() {
			defer callers.Done()
			job, err := s.StartDayRecheck(ctx, originalID, 1)
			if err != nil {
				t.Error(err)
				return
			}
			queued <- job
		}()
	}
	callers.Wait()
	close(queued)
	var recheckID int64
	for job := range queued {
		if recheckID != 0 && job.ID != recheckID || job.ID == jobs[0].ID || job.RecheckReportID == nil || *job.RecheckReportID != originalID || job.PeriodStart != result.PeriodStart || job.ResultReportID != nil {
			t.Fatalf("复评身份或在途去重不正确: %+v", job)
		}
		recheckID = job.ID
	}
	markState(recheckID, "running")
	if _, err = s.ImportJob(ctx, recheckID, bundle); !errors.Is(err, ErrConflict) {
		t.Fatal("复评不能走自动发布接口", err)
	}
	draftRun, err := s.ImportDayJobDraft(ctx, recheckID, bundle)
	if err != nil || draftRun.ID == originalRun.ID {
		t.Fatal("复评必须生成独立草稿，即使结果包完全相同", err)
	}
	if again, err := s.ImportDayJobDraft(ctx, recheckID, bundle); err != nil || again.ID != draftRun.ID {
		t.Fatal("同任务同包必须幂等", err)
	}
	changed := bundle
	changed.SourceName = "day-recheck-other-payload"
	if _, err = s.ImportDayJobDraft(ctx, recheckID, changed); !errors.Is(err, ErrConflict) {
		t.Fatal("复评任务不能静默替换已绑定结果", err)
	}
	listed, total, err := s.Jobs(ctx, 1, 20, JobFilter{State: "running"})
	if err != nil || total != 1 || listed[0].ID != recheckID || listed[0].ResultReportID == nil {
		t.Fatal("进度列表必须能精确定位复评草稿", err)
	}
	draftID := *listed[0].ResultReportID
	draft, err := s.Get(ctx, draftID, 0)
	if err != nil || draft.PublicationStatus != "draft" {
		t.Fatal("管理员应能查看草稿", err)
	}
	if _, err = s.Get(ctx, draftID, 2); !errors.Is(err, ErrNotFound) {
		t.Fatal("员工不应看到复评草稿", err)
	}
	if _, err = s.Get(ctx, originalID, 2); err != nil {
		t.Fatal("发布前员工应仍可看到原版", err)
	}
	if _, err = s.Publish(ctx, draftID, draft.Revision, 1); !errors.Is(err, ErrConflict) {
		t.Fatal("Worker尚未确认完成的复评不可发布", err)
	}
	markState(recheckID, "completed")
	if _, err = s.Publish(ctx, draftID, draft.Revision, 1); err != nil {
		t.Fatal("管理员应可手动发布复评", err)
	}
	if _, err = s.Get(ctx, draftID, 2); err != nil {
		t.Fatal("发布后员工应看到新版", err)
	}
	if _, err = s.Get(ctx, originalID, 2); !errors.Is(err, ErrNotFound) {
		t.Fatal("发布新版后旧版不得继续对员工开放", err)
	}
	if _, err = s.StartDayRecheck(ctx, originalID, 1); err == nil {
		t.Fatal("已经替换的历史报告不能发起复评")
	}
	repeated, err := s.StartDayRecheck(ctx, draftID, 1)
	if err != nil || repeated.ID == recheckID {
		t.Fatal("完成后针对新版应能重新取数", err)
	}
	markState(repeated.ID, "failed")
	newer, err := s.StartDayRecheck(ctx, draftID, 1)
	if err != nil || newer.ID <= repeated.ID {
		t.Fatal("失败后应能新建复评", err)
	}
	if _, err = s.ResumeJob(ctx, repeated.ID); !errors.Is(err, ErrConflict) {
		t.Fatal("已有更新复评时不可恢复旧失败任务", err)
	}
}
