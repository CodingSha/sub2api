//go:build integration

package zhiyongli

import (
	"context"
	"encoding/json"
	"fmt"
	"testing"
)

func TestCompanyRankUsesPublishedComparableScoresAndTies(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	if _, err := s.db.Exec(`INSERT INTO users(id,email) SELECT n,'rank-'||n||'@example.test' FROM generate_series(4,10) n`); err != nil {
		t.Fatal(err)
	}
	var run int64
	if err := s.db.QueryRow(`INSERT INTO zhiyongli_runs(digest,source_name,report_count,created_by) VALUES('rank','rank',9,1) RETURNING id`).Scan(&run); err != nil {
		t.Fatal(err)
	}
	add := func(user int, score, status, version, start string) int64 {
		t.Helper()
		body, _ := json.Marshal(map[string]any{"score": score, "policy_versions": map[string]string{"rules": "1.6", "aggregation": version}})
		var id int64
		if err := s.db.QueryRow(`INSERT INTO zhiyongli_reports(run_id,user_id,period_type,period_start,period_end,revision,body,publication_status) VALUES($1,$2,'week',$3::date,$3::date+7,1,$4::jsonb,$5) RETURNING id`, run, user, start, string(body), status).Scan(&id); err != nil {
			t.Fatal(err)
		}
		return id
	}
	mine := add(2, "78.7", "published", "v2", "2026-11-16")
	add(3, "90.0", "published", "v2", "2026-11-16")
	add(4, "78.7", "published", "v2", "2026-11-16")
	zero := add(5, "0.0", "published", "v2", "2026-11-16")
	add(6, "99.0", "draft", "v2", "2026-11-16")
	add(7, "99.0", "published", "old", "2026-11-16")
	add(8, "99.0", "published", "v2", "2026-11-09")
	add(9, "99.0", "published", "v2", "2026-11-16")
	add(10, "99.0", "published", "v2", "2026-11-16")
	if _, err := s.db.Exec(`INSERT INTO audit_whitelist_users(user_id) VALUES(9)`); err != nil {
		t.Fatal(err)
	}
	t.Setenv("ZHIYONGLI_EXCLUDED_USER_IDS", "10")
	rank, err := s.companyRank(ctx, mine, 2)
	if err != nil || rank.Position != 2 || rank.Total != 4 || !rank.Tied {
		t.Fatal("同周同版已发布结果并列排名", rank, err)
	}
	rank, err = s.companyRank(ctx, zero, 5)
	if err != nil || rank.Position != 4 || rank.Total != 4 || rank.Tied {
		t.Fatal("已评0分须参与排名", rank, err)
	}
	if _, err = s.companyRank(ctx, mine, 3); err != ErrNotFound {
		t.Fatal(fmt.Sprintf("不得查询他人报告排名: %v", err))
	}
}

func TestCurrentJobsSeparateSupersededFailuresWithoutChangingState(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	var old, current int64
	if err := s.db.QueryRow(`INSERT INTO zhiyongli_jobs(user_id,period_type,period_start,period_end,rules_version,created_by,scope_key,state) VALUES(2,'week','2026-11-16','2026-11-23','1.6',1,'old','failed') RETURNING id`).Scan(&old); err != nil {
		t.Fatal(err)
	}
	if err := s.db.QueryRow(`INSERT INTO zhiyongli_jobs(user_id,period_type,period_start,period_end,rules_version,created_by,scope_key,state) VALUES(2,'week','2026-11-16','2026-11-23','1.6',1,'new','completed') RETURNING id`).Scan(&current); err != nil {
		t.Fatal(err)
	}
	for _, view := range []string{"current", "history"} {
		rows, total, err := s.Jobs(ctx, 1, 20, JobFilter{View: view})
		if err != nil || total != 1 || len(rows) != 1 {
			t.Fatal(view, total, err)
		}
		want := current
		if view == "history" {
			want = old
			if rows[0].State != "failed" {
				t.Fatal("历史失败不能伪装成功")
			}
		}
		if rows[0].ID != want || rows[0].LatestJobID != current {
			t.Fatal(view, rows)
		}
	}
	rows, total, err := s.Jobs(ctx, 1, 20, JobFilter{View: "current", State: "failed"})
	if err != nil || total != 0 || len(rows) != 0 {
		t.Fatal("已被替代的失败不能进入当前失败列表", total, err)
	}
}

func TestCurrentCompletedJobFindsOrdinaryReportWithoutRecheckRun(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	var job, run, report int64
	if err := s.db.QueryRow(`INSERT INTO zhiyongli_jobs(user_id,period_type,period_start,period_end,rules_version,created_by,scope_key,state,created_at,updated_at) VALUES(2,'week','2026-11-16','2026-11-23','1.6',1,'weekly','completed',now()-interval '2 hours',now()) RETURNING id`).Scan(&job); err != nil {
		t.Fatal(err)
	}
	if err := s.db.QueryRow(`INSERT INTO zhiyongli_runs(digest,source_name,report_count,created_by) VALUES('ordinary','ordinary',1,1) RETURNING id`).Scan(&run); err != nil {
		t.Fatal(err)
	}
	if err := s.db.QueryRow(`INSERT INTO zhiyongli_reports(run_id,user_id,period_type,period_start,period_end,revision,body,publication_status,created_at) VALUES($1,2,'week','2026-11-16','2026-11-23',1,'{"policy_versions":{"rules":"1.6"}}','published',now()-interval '1 hour') RETURNING id`, run).Scan(&report); err != nil {
		t.Fatal(err)
	}
	check := func(want bool) {
		t.Helper()
		rows, _, err := s.Jobs(ctx, 1, 20, JobFilter{View: "current"})
		if err != nil || len(rows) != 1 {
			t.Fatal(rows, err)
		}
		got := rows[0]
		if want {
			if got.ResultReportID == nil || *got.ResultReportID != report || got.ResultPublicationStatus != "published" {
				t.Fatal("普通已完成任务必须关联真实发布报告", got)
			}
		} else if got.ResultReportID != nil || got.ResultPublicationStatus != "" {
			t.Fatal("不得借用不属于本次执行的报告", got)
		}
	}
	check(true)
	for _, change := range []string{
		`UPDATE zhiyongli_reports SET created_at=now()-interval '3 hours'`,
		`UPDATE zhiyongli_reports SET created_at=now()-interval '1 hour',body='{"policy_versions":{"rules":"old"}}'`,
		`UPDATE zhiyongli_reports SET body='{"policy_versions":{"rules":"1.6"}}',period_end='2026-11-24'`,
		`UPDATE zhiyongli_reports SET period_end='2026-11-23'; UPDATE zhiyongli_jobs SET state='running'`,
		`UPDATE zhiyongli_jobs SET state='failed'`,
	} {
		if _, err := s.db.Exec(change); err != nil {
			t.Fatal(err)
		}
		check(false)
	}
	if _, err := s.db.Exec(`UPDATE zhiyongli_jobs SET state='completed',recheck_report_id=$1 WHERE id=$2`, report, job); err == nil {
		t.Fatal("周任务不能伪装成日复评")
	}
}
