//go:build integration

package handler

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/server/middleware"
	"github.com/Wei-Shaw/sub2api/internal/zhiyongli"
	"github.com/Wei-Shaw/sub2api/migrations"
	"github.com/gin-gonic/gin"
	_ "github.com/lib/pq"
)

func TestZhiyongliDayRecheckHTTP(t *testing.T) {
	dsn := os.Getenv("ZHIYONGLI_TEST_DSN")
	if dsn == "" {
		t.Skip("需要显式本地专用测试库")
	}
	url, err := url.Parse(dsn)
	if err != nil || url.Hostname() != "127.0.0.1" || url.Port() != "55414" || url.Path != "/zhiyongli_test" {
		t.Fatal("只能使用专用本地验收数据库")
	}
	admin, err := sql.Open("postgres", dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer admin.Close()
	schema := fmt.Sprintf("zy_day_recheck_%d", time.Now().UnixNano())
	if _, err = admin.Exec("CREATE SCHEMA " + schema); err != nil {
		t.Fatal(err)
	}
	defer admin.Exec("DROP SCHEMA " + schema + " CASCADE")
	query := url.Query()
	query.Set("search_path", schema)
	url.RawQuery = query.Encode()
	db, err := sql.Open("postgres", url.String())
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err = db.Exec(`CREATE TABLE users(id BIGINT PRIMARY KEY,email TEXT UNIQUE,deleted_at TIMESTAMPTZ);
		CREATE TABLE audit_whitelist_users(user_id BIGINT PRIMARY KEY);
		INSERT INTO users(id,email) VALUES(1,'admin@example.test'),(2,'person@example.test')`); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"241_zhiyongli_reports.sql", "245_zhiyongli_jobs.sql", "246_zhiyongli_workday_scope.sql", "248_zhiyongli_daily_reports.sql", "249_zhiyongli_day_rechecks.sql"} {
		migration, err := migrations.FS.ReadFile(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = db.Exec(string(migration)); err != nil {
			t.Fatal(err)
		}
	}
	if _, err = db.Exec(`INSERT INTO zhiyongli_runs(digest,source_name,report_count,created_by) VALUES('synthetic','synthetic',1,1);
		INSERT INTO zhiyongli_reports(run_id,user_id,period_type,period_start,period_end,revision,body,publication_status)
		VALUES(1,2,'day','2026-11-18','2026-11-19',1,'{}','published')`); err != nil {
		t.Fatal(err)
	}
	gin.SetMode(gin.TestMode)
	router := gin.New()
	handler := NewZhiyongliHandler(db)
	router.POST("/api/v1/admin/zhiyongli/reports/:id/recheck", func(c *gin.Context) {
		if c.GetHeader("X-Local-Test-Admin") == "1" {
			c.Set(string(middleware.ContextKeyUser), middleware.AuthSubject{UserID: 1})
		}
		handler.StartDayRecheck(c)
	})
	server := httptest.NewServer(router)
	defer server.Close()
	var jobID int64
	for _, tc := range []struct {
		id, actor, body string
		status          int
	}{
		{"1", "", "", http.StatusUnauthorized},
		{"1", "1", `{"user_id":3}`, http.StatusBadRequest},
		{"999", "1", "", http.StatusNotFound},
		{"1", "1", "", http.StatusOK},
		{"1", "1", "", http.StatusOK},
	} {
		request, err := http.NewRequest(http.MethodPost, server.URL+"/api/v1/admin/zhiyongli/reports/"+tc.id+"/recheck", strings.NewReader(tc.body))
		if err != nil {
			t.Fatal(err)
		}
		request.Header.Set("X-Local-Test-Admin", tc.actor)
		reply, err := server.Client().Do(request)
		if err != nil {
			t.Fatal(err)
		}
		var body struct {
			Message string        `json:"message"`
			Data    zhiyongli.Job `json:"data"`
		}
		err = json.NewDecoder(reply.Body).Decode(&body)
		reply.Body.Close()
		if err != nil || reply.StatusCode != tc.status {
			t.Fatalf("复评响应不符合预期：%d != %d，%s，%v", reply.StatusCode, tc.status, body.Message, err)
		}
		if tc.status == http.StatusOK {
			job := body.Data
			if job.ID <= 0 || job.UserID != 2 || job.PeriodStart != "2026-11-18" || job.State != "queued" || job.RecheckReportID == nil || *job.RecheckReportID != 1 || jobID != 0 && jobID != job.ID {
				t.Fatalf("复评登记响应不符合任务契约：%+v", job)
			}
			jobID = job.ID
		}
	}
}

// 真实冻结候选经当前HTTP入口和Worker导入函数入库；不会调用模型或连接生产。
func TestZhiyongliRealCandidateHTTPImport(t *testing.T) {
	dsn, input := os.Getenv("ZHIYONGLI_TEST_DSN"), os.Getenv("ZHIYONGLI_QUALIFIED_BUNDLE")
	python, output := os.Getenv("ZHIYONGLI_ACCEPTANCE_PYTHON"), os.Getenv("ZHIYONGLI_ACCEPTANCE_OUTPUT")
	if dsn == "" || input == "" || python == "" || output == "" {
		t.Skip("需要显式本地数据库、真实候选及Python执行器")
	}
	u, err := url.Parse(dsn)
	if err != nil || u.Hostname() != "127.0.0.1" || u.Port() != "55414" || u.Path != "/zhiyongli_test" {
		t.Fatal("只能使用专用本地验收数据库")
	}
	raw, err := os.ReadFile(input)
	if err != nil {
		t.Fatal(err)
	}
	var bundle zhiyongli.Bundle
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err = decoder.Decode(&bundle); err != nil {
		t.Fatal("真实候选不能被当前接口消费", err)
	}
	if len(bundle.Reports) != 1 {
		t.Fatal("验收需要完整单人候选")
	}
	want := bundle.Reports[0]
	draftOnly := os.Getenv("ZHIYONGLI_ACCEPTANCE_DRAFT_ONLY") == "1"
	if draftOnly && want.PeriodType != "day" {
		t.Fatal("受限试评只接受日评候选")
	}
	wantScore, wantStatus := want.Score, want.ScoreStatus
	if err = want.Validate(); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(wantScore, want.Score) || wantStatus != want.ScoreStatus {
		t.Fatal("两端评分重算不一致")
	}
	admin, err := sql.Open("postgres", dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer admin.Close()
	schema := fmt.Sprintf("zy_real_import_%d", time.Now().UnixNano())
	if _, err = admin.Exec("CREATE SCHEMA " + schema); err != nil {
		t.Fatal(err)
	}
	defer admin.Exec("DROP SCHEMA " + schema + " CASCADE")
	q := u.Query()
	q.Set("options", "-csearch_path="+schema)
	u.RawQuery = q.Encode()
	db, err := sql.Open("postgres", u.String())
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err = db.Exec(`CREATE TABLE users(id BIGINT PRIMARY KEY,email TEXT UNIQUE,deleted_at TIMESTAMPTZ); CREATE TABLE audit_whitelist_users(user_id BIGINT PRIMARY KEY)`); err != nil {
		t.Fatal(err)
	}
	if _, err = db.Exec(`INSERT INTO users(id,email) VALUES(1,'acceptance-admin@example.test'),(2,$1)`, want.UserEmail); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"241_zhiyongli_reports.sql", "245_zhiyongli_jobs.sql", "246_zhiyongli_workday_scope.sql", "247_zhiyongli_task_cases.sql", "248_zhiyongli_daily_reports.sql", "249_zhiyongli_day_rechecks.sql"} {
		migration, e := migrations.FS.ReadFile(name)
		if e != nil {
			t.Fatal(e)
		}
		if _, e = db.Exec(string(migration)); e != nil {
			t.Fatal(e)
		}
	}
	scope, err := json.Marshal(want.WorkdayScope)
	if err != nil {
		t.Fatal(err)
	}
	var jobID int64
	err = db.QueryRow(`INSERT INTO zhiyongli_jobs(user_id,period_type,period_start,period_end,rules_version,created_by,scope_key,workday_scope,state) VALUES(2,$1,$2,$3,$4,1,'real-frozen-acceptance',$5::jsonb,'running') RETURNING id`, want.PeriodType, want.PeriodStart, want.PeriodEnd, want.PolicyVersions.Rules, string(scope)).Scan(&jobID)
	if err != nil {
		t.Fatal(err)
	}
	keyFile := filepath.Join(t.TempDir(), "worker.key")
	key := strings.Repeat("local-acceptance-only-", 3)
	if err = os.WriteFile(keyFile, []byte(key), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("ZHIYONGLI_WORKER_KEY_FILE", keyFile)
	gin.SetMode(gin.TestMode)
	router := gin.New()
	h := NewZhiyongliHandler(db)
	router.POST("/api/v1/internal/zhiyongli/jobs/:id/import", h.ImportJob)
	router.POST("/api/v1/internal/zhiyongli/jobs/:id/import-draft", h.ImportDayJobDraft)
	server := httptest.NewServer(router)
	defer server.Close()
	// 使用正式Worker的HTTP发送、幂等回执和完成状态写入逻辑。
	command := exec.Command(python, "-c", `
import json,os
from pathlib import Path
import psycopg
from worker import import_candidate,importer,update
root=Path(os.environ['ZY_LOCAL_IMPORT_OUTPUT'])
root.mkdir(parents=True,exist_ok=True)
bundle=json.loads(Path(os.environ['ZHIYONGLI_QUALIFIED_BUNDLE']).read_text())
job={'id':int(os.environ['ZY_LOCAL_JOB_ID'])}
send=importer(os.environ['ZY_LOCAL_URL'],os.environ['ZHIYONGLI_WORKER_KEY_FILE'],
              draft_only=os.environ.get('ZHIYONGLI_ACCEPTANCE_DRAFT_ONLY')=='1')
first=import_candidate(root,job,bundle,send)
assert import_candidate(root,job,bundle,send)==first
with psycopg.connect(os.environ['ZHIYONGLI_TEST_DSN'],autocommit=True) as db:
 update(db,job['id'],'completed','done',1,1,'真实冻结候选已通过当前Go接口导入并核验')
print('真实HTTP导入、同包幂等与Worker完成状态已验证')
`)
	command.Env = append(os.Environ(), "ZHIYONGLI_TEST_DSN="+u.String(), "ZY_LOCAL_URL="+server.URL, "ZY_LOCAL_IMPORT_OUTPUT="+filepath.Join(output, schema), fmt.Sprintf("ZY_LOCAL_JOB_ID=%d", jobID))
	if result, e := command.CombinedOutput(); e != nil {
		t.Fatalf("Worker导入失败: %v %s", e, result)
	}
	store := zhiyongli.NewStore(db)
	list, count, err := store.List(context.Background(), zhiyongli.Filter{Page: 1, PageSize: 20})
	if err != nil || count != 1 || len(list) != 1 {
		t.Fatal("真实报告未入库或生成重复修订", err)
	}
	got, err := store.Get(context.Background(), list[0].ID, 0)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got.Score, want.Score) || got.ScoreStatus != want.ScoreStatus || len(got.Items) != len(want.Items) || !reflect.DeepEqual(got.WorkdayScope, want.WorkdayScope) {
		t.Fatal("入库后评分、分项或评审范围丢失")
	}
	for i, item := range got.Items {
		if item.Code != want.Items[i].Code || item.State != want.Items[i].State || !reflect.DeepEqual(item.Exact, want.Items[i].Exact) || len(item.Evidence) != len(want.Items[i].Evidence) || item.Reason != want.Items[i].Reason {
			t.Fatal("分项结论或证据入库失真", item.Code)
		}
	}
	if want.PeriodType == "day" {
		// omitempty空目录经JSONB往返后可为nil；比较公开合同，正文及调用信息仍须逐字一致。
		wantDaily, _ := json.Marshal(want.DailyReview)
		gotDaily, _ := json.Marshal(got.DailyReview)
		wantPublication := "published"
		if draftOnly {
			wantPublication = "draft"
		}
		if !bytes.Equal(wantDaily, gotDaily) || got.PublicationStatus != wantPublication {
			t.Fatal("日评对话原文、工作流或发布状态不符")
		}
		if _, err = store.Get(context.Background(), got.ID, 2); (draftOnly && err != zhiyongli.ErrNotFound) || (!draftOnly && err != nil) {
			t.Fatal("本人日评可见性与导入方式不符", err)
		}
		if _, err = store.Get(context.Background(), got.ID, 3); err != zhiyongli.ErrNotFound {
			t.Fatal("不能读取他人日评分", err)
		}
	} else {
		if want.TaskReview == nil || got.TaskReview == nil || len(want.TaskReview.Cases) == 0 {
			t.Fatal("真实候选必须包含可读取的任务案例")
		}
		for _, wantCase := range want.TaskReview.Cases {
			var records []zhiyongli.TaskRecord
			for page := 1; len(records) < len(wantCase.Records); page++ {
				part, e := store.GetTaskCase(context.Background(), got.ID, 0, wantCase.ID, page, 2)
				if e != nil || part.Total != len(wantCase.Records) || len(part.Records) == 0 {
					t.Fatal("真实案例分页记录丢失", e)
				}
				records = append(records, part.Records...)
			}
			if !reflect.DeepEqual(records, wantCase.Records) {
				t.Fatal("真实案例原文、归属、时间或哈希入库失真")
			}
			if _, e := store.GetTaskCase(context.Background(), got.ID, 2, wantCase.ID, 1, 2); e != zhiyongli.ErrNotFound {
				t.Fatal("草稿案例被员工提前读取", e)
			}
		}
	}
	_, runs, err := store.Runs(context.Background(), 1, 20)
	if err != nil || runs != 1 {
		t.Fatal("同包产生重复Run", err)
	}
	var state, phase string
	if err = db.QueryRow("SELECT state,phase FROM zhiyongli_jobs WHERE id=$1", jobID).Scan(&state, &phase); err != nil || state != "completed" || phase != "done" {
		t.Fatal("任务没有正常完成", err)
	}
	summary := map[string]any{"status": "passed", "real_go_handler": true, "real_postgres": true, "production_touched": false, "report_count": count, "run_count": runs, "score": got.Score, "score_status": got.ScoreStatus, "items": len(got.Items), "workday_scope_preserved": true, "task_case_records_preserved": want.TaskReview != nil, "draft_case_access_denied": want.PeriodType != "day", "daily_context_preserved": want.PeriodType == "day", "job_state": state, "job_phase": phase, "publication_status": got.PublicationStatus, "bundle_path": input, "worker_receipts": filepath.Join(output, schema)}
	evidence, err := json.MarshalIndent(summary, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(output, "go-import-acceptance.json"), evidence, 0600); err != nil {
		t.Fatal(err)
	}
	t.Logf("真实候选: %d项、评分状态%s、HTTP导入、同包幂等、范围保留、任务%s", len(got.Items), got.ScoreStatus, state)
}
