//go:build integration

package zhiyongli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestTaskCaseRevisionPrivacyAndPagination(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	b := Bundle{SchemaVersion: 1, SourceName: "task-cases", Reports: []Result{taskSample()}}
	run, e := s.Import(ctx, b, 1)
	if e != nil {
		t.Fatal(e)
	}
	again, e := s.Import(ctx, b, 1)
	if e != nil || again.ID != run.ID {
		t.Fatal("重复导入不幂等", e)
	}
	if len(b.Reports[0].TaskReview.Cases[0].Records) != 2 {
		t.Fatal("导入修改了调用方的来源")
	}
	list, _, e := s.List(ctx, Filter{Page: 1, PageSize: 20})
	if e != nil || list[0].TaskReview != nil {
		t.Fatal("列表泄露案例", e)
	}
	id := list[0].ID
	cid := b.Reports[0].TaskReview.Cases[0].ID
	if _, e = s.GetTaskCase(ctx, id, 2, cid, 1, 1); !errors.Is(e, ErrNotFound) {
		t.Fatal("个人可读草稿", e)
	}
	detail, e := s.Get(ctx, id, 0)
	if e != nil || len(detail.TaskReview.Cases[0].Records) != 0 {
		t.Fatal("报告详情预载了全部原文", e)
	}
	if _, e = s.Publish(ctx, id, 1, 1); e != nil {
		t.Fatal(e)
	}
	if _, e = s.GetTaskCase(ctx, id, 2, cid, 1, 51); e == nil {
		t.Fatal("超过合同的页大小被接受")
	}
	empty, err := s.GetTaskCase(ctx, id, 2, cid, 3, 1)
	if err != nil || len(empty.Records) != 0 || empty.Total != 2 {
		t.Fatal("末页后记录错误", err)
	}
	if _, err = s.db.Exec(`INSERT INTO audit_whitelist_users(user_id) VALUES(2)`); err != nil {
		t.Fatal(err)
	}
	if _, err = s.GetTaskCase(ctx, id, 2, cid, 1, 1); !errors.Is(err, ErrTaskCaseForbidden) {
		t.Fatal("白名单仍可读个人案例", err)
	}
	if _, err = s.db.Exec(`DELETE FROM audit_whitelist_users WHERE user_id=2`); err != nil {
		t.Fatal(err)
	}
	t.Setenv("ZHIYONGLI_EXCLUDED_USER_IDS", "2")
	if _, err = s.GetTaskCase(ctx, id, 2, cid, 1, 1); !errors.Is(err, ErrTaskCaseForbidden) {
		t.Fatal("排除账户仍可读个人案例", err)
	}
	t.Setenv("ZHIYONGLI_EXCLUDED_USER_IDS", "")
	first, e := s.GetTaskCase(ctx, id, 2, cid, 1, 1)
	if e != nil || first.Total != 2 || len(first.Records) != 1 {
		t.Fatal("分页记录错误", e)
	}
	second, e := s.GetTaskCase(ctx, id, 2, cid, 2, 1)
	if e != nil || first.Records[0].ID == second.Records[0].ID {
		t.Fatal("分页重复", e)
	}
	if _, e = s.GetTaskCase(ctx, id, 3, cid, 1, 1); !errors.Is(e, ErrNotFound) {
		t.Fatal("跨人读取", e)
	}
	b.Reports[0].TaskReview.Cases[0].Outcome = "复核后仍无最终验收"
	if _, e = s.Import(ctx, b, 1); e != nil {
		t.Fatal(e)
	}
	list, _, _ = s.List(ctx, Filter{Page: 1, PageSize: 20})
	if _, e = s.Publish(ctx, list[0].ID, 2, 1); e != nil {
		t.Fatal(e)
	}
	if _, e = s.GetTaskCase(ctx, id, 2, cid, 1, 1); !errors.Is(e, ErrNotFound) {
		t.Fatal("旧修订仍可个人读取", e)
	}
	if _, e = s.GetTaskCase(ctx, id, 0, cid, 1, 1); e != nil {
		t.Fatal("管理员追溯旧修订失败", e)
	}
}

// Python实际导出链的产物，不用Go样例替代字段跨语言验证。
func TestPythonPresentationFixtureStrictImport(t *testing.T) {
	path := os.Getenv("ZHIYONGLI_PRESENTATION_FIXTURE")
	if path == "" {
		t.Skip("requires explicit synthetic Python presentation fixture")
	}
	if !filepath.IsAbs(path) {
		t.Fatal("Python合成说明fixture必须为绝对路径")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var bundle Bundle
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err = decoder.Decode(&bundle); err != nil {
		t.Fatal("Python正式字段严格解码失败", err)
	}
	if err = decoder.Decode(new(any)); err != io.EOF {
		t.Fatal("fixture必须仅含一个bundle", err)
	}
	if len(bundle.Reports) != 1 {
		t.Fatal("桥接fixture只接受单份合成周报告")
	}
	report := &bundle.Reports[0]
	if report.TaskReview == nil || report.TaskReview.Version != TaskReviewVersion || (report.TaskReview.Status != "case_checked" && report.TaskReview.Status != "week_checked" && report.TaskReview.Status != "generated") || len(report.TaskReview.Cases) == 0 {
		t.Fatal("fixture未包含Python正式导出的任务说明")
	}
	if err = report.Validate(); err != nil {
		t.Fatal("Python说明合同不通过", err)
	}
	// 用户仅在隔离测试schema创建，保留Python产物原邮箱，不改其报告字段。
	s := testStore(t)
	ctx := context.Background()
	if _, err = s.db.ExecContext(ctx, "UPDATE users SET email=$1 WHERE id=2", report.UserEmail); err != nil {
		t.Fatal(err)
	}
	before, err := json.Marshal(bundle)
	if err != nil {
		t.Fatal(err)
	}
	run, err := s.Import(ctx, bundle, 1)
	if err != nil {
		t.Fatal("Python说明入库失败", err)
	}
	again, err := s.Import(ctx, bundle, 1)
	if err != nil || again.ID != run.ID {
		t.Fatal("Python同包导入不幂等", err)
	}
	after, err := json.Marshal(bundle)
	if err != nil || !bytes.Equal(before, after) {
		t.Fatal("导入改变Python说明原始对象", err)
	}
	rows, total, err := s.List(ctx, Filter{Page: 1, PageSize: 20})
	if err != nil || total != 1 || len(rows) != 1 || rows[0].TaskReview != nil {
		t.Fatal("列表泄露说明或未入库", err)
	}
	id, revision := rows[0].ID, rows[0].Revision
	detail, err := s.Get(ctx, id, 0)
	if err != nil || detail.TaskReview == nil || len(detail.TaskReview.Cases) != len(report.TaskReview.Cases) {
		t.Fatal("管理员说明摘要读取失败", err)
	}
	for _, c := range detail.TaskReview.Cases {
		if len(c.Records) != 0 {
			t.Fatal("详情预载记录正文")
		}
	}
	if _, err = s.Get(ctx, id, 2); !errors.Is(err, ErrNotFound) {
		t.Fatal("未发布个人详情可见", err)
	}
	for _, original := range report.TaskReview.Cases {
		if _, err = s.GetTaskCase(ctx, id, 2, original.ID, 1, 1); !errors.Is(err, ErrNotFound) {
			t.Fatal("未发布个人案例可见", err)
		}
		all := []TaskRecord{}
		for page := 1; len(all) < len(original.Records); page++ {
			got, e := s.GetTaskCase(ctx, id, 0, original.ID, page, 1)
			if e != nil || got.Total != len(original.Records) || len(got.Records) != 1 {
				t.Fatal("管理员分页丢记录", e)
			}
			all = append(all, got.Records...)
		}
		if !reflect.DeepEqual(all, original.Records) {
			t.Fatal("Python原记录跨语言存储/分页后改变")
		}
	}
	if _, err = s.Publish(ctx, id, revision, 1); err != nil {
		t.Fatal(err)
	}
	if _, err = s.Get(ctx, id, 2); err != nil {
		t.Fatal("发布后本人详情不可见", err)
	}
	for _, original := range report.TaskReview.Cases {
		got, e := s.GetTaskCase(ctx, id, 2, original.ID, 1, 1)
		if e != nil || len(got.Records) != 1 || !reflect.DeepEqual(got.Records[0], original.Records[0]) {
			t.Fatal("发布后本人案例不可见或改变", e)
		}
		if _, e = s.GetTaskCase(ctx, id, 3, original.ID, 1, 1); !errors.Is(e, ErrNotFound) {
			t.Fatal("其他人可读案例", e)
		}
	}
}
