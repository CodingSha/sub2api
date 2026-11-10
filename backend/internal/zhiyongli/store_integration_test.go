//go:build integration

package zhiyongli

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"sync"
	"testing"
	"time"

	"github.com/Wei-Shaw/sub2api/migrations"
	_ "github.com/lib/pq"
)

func testStore(t *testing.T) *Store {
	t.Helper()
	dsn := os.Getenv("ZHIYONGLI_TEST_DSN")
	if dsn == "" {
		t.Skip("requires dedicated local test database")
	}
	u, e := url.Parse(dsn)
	if e != nil || u.Hostname() != "127.0.0.1" || u.Path != "/zhiyongli_test" {
		t.Fatal("integration test requires 127.0.0.1/zhiyongli_test")
	}
	db, e := sql.Open("postgres", dsn)
	if e != nil {
		t.Fatal(e)
	}
	schema := fmt.Sprintf("zhiyongli_test_%d", time.Now().UnixNano())
	if _, e = db.Exec("CREATE SCHEMA " + schema); e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { db.Exec("DROP SCHEMA " + schema + " CASCADE"); db.Close() })
	q := u.Query()
	q.Set("search_path", schema)
	u.RawQuery = q.Encode()
	scoped, e := sql.Open("postgres", u.String())
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { scoped.Close() })
	if _, e = scoped.Exec(`CREATE TABLE users(id BIGINT PRIMARY KEY,email TEXT UNIQUE,deleted_at TIMESTAMPTZ); INSERT INTO users(id,email) VALUES(1,'admin@example.test'),(2,'person@example.test'),(3,'other@example.test')`); e != nil {
		t.Fatal(e)
	}
	migration, e := migrations.FS.ReadFile("241_zhiyongli_reports.sql")
	if e != nil {
		t.Fatal(e)
	}
	if _, e = scoped.Exec(string(migration)); e != nil {
		t.Fatal(e)
	}
	if _, e = scoped.Exec(`CREATE TABLE audit_whitelist_users(user_id BIGINT PRIMARY KEY REFERENCES users(id))`); e != nil {
		t.Fatal(e)
	}
	migration, e = migrations.FS.ReadFile("245_zhiyongli_jobs.sql")
	if e != nil {
		t.Fatal(e)
	}
	if _, e = scoped.Exec(string(migration)); e != nil {
		t.Fatal(e)
	}
	migration, e = migrations.FS.ReadFile("246_zhiyongli_workday_scope.sql")
	if e != nil {
		t.Fatal(e)
	}
	if _, e = scoped.Exec(string(migration)); e != nil {
		t.Fatal(e)
	}
	return NewStore(scoped)
}

func TestQualifiedMonthSourcesStayPrivate(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	r := qualifiedMonth(3)
	if _, err := s.Import(ctx, Bundle{SchemaVersion: 1, SourceName: "qualified-month", Reports: []Result{r}}, 1); err != nil {
		t.Fatal(err)
	}
	rows, _, err := s.List(ctx, Filter{Page: 1, PageSize: 20})
	if err != nil || len(rows) != 1 {
		t.Fatal(err)
	}
	if rows[0].WeeklyReports != nil || rows[0].Items != nil {
		t.Fatal("列表泄露后台周源")
	}
	detail, err := s.Get(ctx, rows[0].ID, 0)
	if err != nil || detail.WeeklyReports != nil || len(detail.Items[0].Evidence) == 0 {
		t.Fatal("详情周源隔离或证据丢失", err)
	}
	var raw []byte
	if err = s.db.QueryRowContext(ctx, "SELECT body FROM zhiyongli_reports WHERE id=$1", rows[0].ID).Scan(&raw); err != nil {
		t.Fatal(err)
	}
	var saved Result
	if err = json.Unmarshal(raw, &saved); err != nil || len(saved.WeeklyReports) != 5 {
		t.Fatal("后台重算来源未持久化", err)
	}
}
func TestStoreImportPublishIsolation(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	b := Bundle{SchemaVersion: 1, SourceName: "validated-week", Reports: []Result{sampleResult()}}
	run, e := s.Import(ctx, b, 1)
	if e != nil {
		t.Fatal(e)
	}
	again, e := s.Import(ctx, b, 1)
	if e != nil || again.ID != run.ID {
		t.Fatalf("idempotency failed: %v", e)
	}
	list, total, e := s.List(ctx, Filter{Page: 1, PageSize: 20})
	if e != nil || total != 1 || len(list) != 1 {
		t.Fatal("missing imported report", e)
	}
	id := list[0].ID
	if _, e = s.Get(ctx, id, 2); !errors.Is(e, ErrNotFound) {
		t.Fatal("draft visible to owner")
	}
	if _, e = s.Publish(ctx, id, 99, 1); !errors.Is(e, ErrConflict) {
		t.Fatal("stale revision accepted")
	}
	r, e := s.Publish(ctx, id, 1, 1)
	if e != nil {
		t.Fatal(e)
	}
	if len(r.Items[0].Evidence) != 1 {
		t.Fatal("evidence lost")
	}
	repeated, e := s.Publish(ctx, id, 1, 1)
	if e != nil || len(repeated.Items[0].Evidence) != 1 {
		t.Fatal("重复发布必须返回完整的同一报告", e)
	}
	if _, e = s.Get(ctx, id, 3); !errors.Is(e, ErrNotFound) {
		t.Fatal("cross-user access accepted")
	}
	if _, e = s.Get(ctx, id, 2); e != nil {
		t.Fatal("published report not visible", e)
	}
	b.Reports[0].Items[0].Reason = "复核后的新理由"
	if _, e = s.Import(ctx, b, 1); e != nil {
		t.Fatal(e)
	}
	list, _, _ = s.List(ctx, Filter{Page: 1, PageSize: 20})
	newID := list[0].ID
	// 多个发布请求只能留下一个当前版本；重复发布同一修订幂等。
	var wg sync.WaitGroup
	errs := make(chan error, 4)
	for i := 0; i < 4; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); _, e := s.Publish(ctx, newID, 2, 1); errs <- e }()
	}
	wg.Wait()
	close(errs)
	for e := range errs {
		if e != nil {
			t.Fatal(e)
		}
	}
	if _, e = s.Get(ctx, id, 2); !errors.Is(e, ErrNotFound) {
		t.Fatal("superseded report visible")
	}
	list, total, e = s.List(ctx, Filter{UserID: 2, Page: 1, PageSize: 20})
	if e != nil || total != 1 || list[0].ID != newID {
		t.Fatal("current publication mismatch", e)
	}
	if _, e = s.Publish(ctx, id, 1, 1); !errors.Is(e, ErrConflict) {
		t.Fatal("historical report republished")
	}
	// 身份错误必须回滚整包和接入记录，不留下半批结果。
	b.SourceName = "invalid-batch"
	b.Reports = append(b.Reports, sampleResult())
	b.Reports[1].UserEmail = "missing@example.test"
	if _, e = s.Import(ctx, b, 1); e == nil {
		t.Fatal("unknown identity accepted")
	}
	_, total, e = s.Runs(ctx, 1, 20)
	if e != nil || total != 2 {
		t.Fatal("partial import remained", e)
	}
}

func TestNativePipelineBundle(t *testing.T) {
	path := os.Getenv("ZHIYONGLI_VALIDATED_BUNDLE")
	if path == "" {
		t.Skip("requires explicitly supplied reviewed native results")
	}
	raw, e := os.ReadFile(path)
	if e != nil {
		t.Fatal(e)
	}
	var bundle Bundle
	if e = json.Unmarshal(raw, &bundle); e != nil {
		t.Fatal(e)
	}
	s := testStore(t)
	for i, r := range bundle.Reports {
		if _, e = s.db.Exec(`INSERT INTO users(id,email) VALUES($1,$2) ON CONFLICT(email) DO NOTHING`, int64(10+i), r.UserEmail); e != nil {
			t.Fatal(e)
		}
	}
	if _, e = s.Import(context.Background(), bundle, 1); e != nil {
		t.Fatal(e)
	}
	list, total, e := s.List(context.Background(), Filter{Page: 1, PageSize: 100})
	if e != nil || total != int64(len(bundle.Reports)) {
		t.Fatal("native batch mismatch", e)
	}
	var checks []struct {
		User       string `json:"user"`
		Score      string `json:"score"`
		PeriodType string `json:"period_type"`
	}
	checkRaw, e := os.ReadFile(os.Getenv("ZHIYONGLI_NATIVE_CHECK"))
	if e != nil {
		t.Fatal(e)
	}
	if e = json.Unmarshal(checkRaw, &checks); e != nil {
		t.Fatal(e)
	}
	for _, r := range list {
		found := false
		for _, c := range checks {
			if r.UserEmail == c.User+"@apsat.com" && (c.PeriodType == "" || c.PeriodType == r.PeriodType) {
				found = true
				if r.Score == nil || *r.Score != c.Score {
					t.Fatalf("native score diverged for %s", r.UserEmail)
				}
			}
		}
		if !found {
			t.Fatal("unexpected native identity")
		}
	}
}
