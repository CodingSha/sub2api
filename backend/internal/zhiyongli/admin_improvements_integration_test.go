//go:build integration

package zhiyongli

import (
	"context"
	"errors"
	"fmt"
	"testing"
)

func TestBatchPublishRollsBackConflictAndIsIdempotent(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	r := sampleResult()
	other := sampleResult()
	other.UserEmail = "other@example.test"
	if _, err := s.Import(ctx, Bundle{SchemaVersion: 1, SourceName: "batch-check", Reports: []Result{r, other}}, 1); err != nil {
		t.Fatal(err)
	}
	rows, total, err := s.List(ctx, Filter{Page: 1, PageSize: 100})
	if err != nil || total != 2 {
		t.Fatal(err, total)
	}
	input := BatchPublishInput{PeriodType: r.PeriodType, PeriodStart: r.PeriodStart}
	for _, row := range rows {
		input.Reports = append(input.Reports, PublishReference{ID: row.ID, Revision: row.Revision})
	}
	input.Reports[1].Revision++
	if _, err = s.BatchPublish(ctx, input, 1); !errors.Is(err, ErrConflict) {
		t.Fatal("未拒绝陈旧修订", err)
	}
	var published int
	if err = s.db.QueryRow("select count(*) from zhiyongli_reports where publication_status='published'").Scan(&published); err != nil || published != 0 {
		t.Fatal("整批未回滚", err, published)
	}
	input.Reports[1].Revision--
	for i := 0; i < 2; i++ {
		result, err := s.BatchPublish(ctx, input, 1)
		if err != nil || result.PublishedCount != 2 {
			t.Fatal("发布或重复提交失败", err, result)
		}
	}
	for _, row := range rows {
		if _, err = s.Get(ctx, row.ID, row.UserID); err != nil {
			t.Fatal("本人不可见", err)
		}
	}
	input.PeriodStart = "2026-09-14"
	if _, err = s.BatchPublish(ctx, input, 1); err == nil {
		t.Fatal("混合周期被接受")
	}
	input.PeriodStart = r.PeriodStart
	input.Reports[1] = input.Reports[0]
	if _, err = s.BatchPublish(ctx, input, 1); err == nil {
		t.Fatal("重复报告被接受")
	}
}

func TestJobSortingUsesActualLockAcrossPagesAndFilters(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	// 活动任务故意使用最小ID；其余22条覆盖第二页和遗留running。
	for i := 1; i <= 23; i++ {
		_, err := s.db.Exec(`INSERT INTO zhiyongli_jobs(user_id,period_type,period_start,period_end,rules_version,created_by,scope_key,state) VALUES(2,'week','2026-09-07','2026-09-14','1.6',1,$1,$2)`, fmt.Sprint(i), map[bool]string{true: "running", false: "queued"}[i <= 2])
		if err != nil {
			t.Fatal(err)
		}
	}
	conn, err := s.db.Conn(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err = conn.ExecContext(ctx, "SELECT pg_advisory_lock(245,1)"); err != nil {
		t.Fatal(err)
	}
	defer conn.ExecContext(ctx, "SELECT pg_advisory_unlock(245,1)")
	rows, total, err := s.Jobs(ctx, 1, 20)
	if err != nil || total != 23 || len(rows) != 20 {
		t.Fatal(err, total)
	}
	if rows[0].ID != 1 || !rows[0].Active || rows[1].ID != 2 || rows[1].Active {
		t.Fatal("真实活动未排首位或遗留running误标", rows[:2])
	}
	rows, total, err = s.Jobs(ctx, 1, 20, JobFilter{State: "running", Search: "PERSON@EXAMPLE"})
	if err != nil || total != 2 || len(rows) != 2 {
		t.Fatal(err, total)
	}
	rows, total, err = s.Jobs(ctx, 1, 20, JobFilter{Search: "nobody"})
	if err != nil || total != 0 || len(rows) != 0 {
		t.Fatal(err, total)
	}
	if _, _, err = s.Jobs(ctx, 1, 20, JobFilter{State: "invalid"}); err == nil {
		t.Fatal("无效状态被接受")
	}
}
