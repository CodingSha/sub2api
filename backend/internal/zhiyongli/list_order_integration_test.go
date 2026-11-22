//go:build integration

package zhiyongli

import (
	"context"
	"fmt"
	"testing"
)

func TestReportOrderingAcrossPagesKeepsMissingScoresLast(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	var run int64
	if err := s.db.QueryRow(`INSERT INTO zhiyongli_runs(digest,source_name,report_count,created_by) VALUES('sort','sort',3,1) RETURNING id`).Scan(&run); err != nil {
		t.Fatal(err)
	}
	for i, score := range []string{`"9.0"`, `"80.0"`, `null`} {
		body := fmt.Sprintf(`{"user_email":"person@example.test","period_type":"week","score":%s,"dimensions":[{"code":"P","score":%s}]}`, score, score)
		if _, err := s.db.Exec(`INSERT INTO zhiyongli_reports(run_id,user_id,period_type,period_start,period_end,revision,body) VALUES($1,2,'week',$2::date,$2::date+7,1,$3::jsonb)`, run, fmt.Sprintf("2026-09-%02d", 7+i*7), body); err != nil {
			t.Fatal(err)
		}
	}
	for _, sort := range []string{"score_desc", "P_desc", "score_asc", "P_asc"} {
		want := []string{"80.0", "9.0", ""}
		if sort == "score_asc" || sort == "P_asc" {
			want[0], want[1] = want[1], want[0]
		}
		seen := map[int64]bool{}
		for page := 1; page <= 3; page++ {
			rows, total, err := s.List(ctx, Filter{Page: page, PageSize: 1, Sort: sort})
			if err != nil || total != 3 || len(rows) != 1 {
				t.Fatal(sort, page, total, err)
			}
			got := ""
			if rows[0].Score != nil {
				got = *rows[0].Score
			}
			if got != want[page-1] || seen[rows[0].ID] {
				t.Fatal("必须完整结果数值排序后分页，空值不能补零或跨页重复", sort, page, got)
			}
			seen[rows[0].ID] = true
		}
	}
	for _, f := range []Filter{{Sort: "score_desc", PeriodType: "day"}, {Sort: "r.id; DROP TABLE users"}} {
		if _, _, err := s.List(ctx, f); err == nil {
			t.Fatal("拒绝按隐藏日总分排序及任意SQL", f)
		}
	}
}

func TestJobExplicitOrderingKeepsFiltersAndPagination(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	for i := 0; i < 3; i++ {
		if _, err := s.db.Exec(`INSERT INTO zhiyongli_jobs(user_id,period_type,period_start,period_end,rules_version,created_by,scope_key) VALUES(2,'week',$1::date,$1::date+7,'1.6',1,'sort')`, fmt.Sprintf("2026-09-%02d", 7+i*7)); err != nil {
			t.Fatal(err)
		}
	}
	for page := 1; page <= 3; page++ {
		rows, total, err := s.Jobs(ctx, page, 1, JobFilter{Sort: "period_asc", State: "queued", Search: "PERSON@EXAMPLE"})
		if err != nil || total != 3 || len(rows) != 1 || rows[0].PeriodStart != fmt.Sprintf("2026-09-%02d", 7+(page-1)*7) {
			t.Fatal("全局排序须保留状态、人员筛选及跨页顺序", page, rows, err)
		}
	}
	if _, _, err := s.Jobs(ctx, 1, 20, JobFilter{Sort: "j.id DESC;"}); err == nil {
		t.Fatal("禁止任意排序SQL")
	}
}
