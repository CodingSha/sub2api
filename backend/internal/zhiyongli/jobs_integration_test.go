//go:build integration

package zhiyongli

import (
	"context"
	"errors"
	"sync"
	"testing"
)

func TestJobIdentityExclusionRecoveryAndImport(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	if _, e := s.db.Exec("INSERT INTO audit_whitelist_users(user_id) VALUES(3)"); e != nil {
		t.Fatal(e)
	}
	input := StartJobs{UserIDs: []int64{2, 3}, PeriodType: "week", PeriodStart: "2026-09-07"}
	if _, e := s.StartJobs(ctx, input, 1); e == nil {
		t.Fatal("排除人员不能进入评审")
	}
	_, total, e := s.Jobs(ctx, 1, 20)
	if e != nil || total != 0 {
		t.Fatal("多账户启动失败须整批回滚", e)
	}
	input.UserIDs = []int64{2}
	first, e := s.StartJobs(ctx, input, 1)
	if e != nil {
		t.Fatal(e)
	}
	again, e := s.StartJobs(ctx, input, 1)
	if e != nil || again[0].ID != first[0].ID {
		t.Fatal("同周期必须复用", e)
	}
	id := first[0].ID
	if _, e = s.db.Exec("UPDATE zhiyongli_jobs SET state='unknown' WHERE id=$1", id); e != nil {
		t.Fatal(e)
	}
	if _, e = s.ResumeJob(ctx, id); !errors.Is(e, ErrConflict) {
		t.Fatal("未知结果不能重新排队", e)
	}
	if _, e = s.db.Exec("UPDATE zhiyongli_jobs SET state='failed' WHERE id=$1", id); e != nil {
		t.Fatal(e)
	}
	if j, e := s.ResumeJob(ctx, id); e != nil || j.State != "queued" {
		t.Fatal("明确失败应可恢复校验", e)
	}
	r := withObservation(sampleResult(), 1, []string{"2026-09-07"})
	r.PolicyVersions.Rules = RulesVersion
	r.PolicyVersions.Review = ReviewPolicyVersion
	r.PolicyVersions.Scope = WorkdayScopeVersion
	r.WorkdayScope = first[0].WorkdayScope
	r.Eligibility.Version = CurrentEligibilityVersion
	b := Bundle{SchemaVersion: 1, SourceName: "job-import-test", Reports: []Result{r}}
	if _, e = s.ImportJob(ctx, id, b); !errors.Is(e, ErrConflict) {
		t.Fatal("仅执行中任务可接入", e)
	}
	if _, e = s.db.Exec("UPDATE zhiyongli_jobs SET state='running' WHERE id=$1", id); e != nil {
		t.Fatal(e)
	}
	b.Reports[0].UserEmail = "other@example.test"
	if _, e = s.ImportJob(ctx, id, b); e == nil {
		t.Fatal("不能接入他人报告")
	}
	b.Reports[0].UserEmail = r.UserEmail
	run, e := s.ImportJob(ctx, id, b)
	if e != nil {
		t.Fatal(e)
	}
	reused, e := s.ImportJob(ctx, id, b)
	if e != nil || reused.ID != run.ID {
		t.Fatal("导入超时后须按摘要复用", e)
	}
	reports, _, e := s.List(ctx, Filter{Page: 1, PageSize: 20})
	if e != nil || len(reports) != 1 || reports[0].PublicationStatus != "draft" {
		t.Fatal("不得自动发布", e)
	}
}

func TestConcurrentJobStartReusesSameJob(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	var wg sync.WaitGroup
	ids := make(chan int64, 4)
	errs := make(chan error, 4)
	for i := 0; i < 4; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			js, e := s.StartJobs(ctx, StartJobs{[]int64{2}, "week", "2026-09-07"}, 1)
			errs <- e
			if e == nil {
				ids <- js[0].ID
			}
		}()
	}
	wg.Wait()
	close(ids)
	close(errs)
	for e := range errs {
		if e != nil {
			t.Fatal(e)
		}
	}
	var first int64
	for id := range ids {
		if first != 0 && id != first {
			t.Fatal("并发启动生成了重复任务")
		}
		first = id
	}
}
