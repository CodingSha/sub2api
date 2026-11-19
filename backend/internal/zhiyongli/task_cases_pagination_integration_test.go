//go:build integration

package zhiyongli

import (
	"bytes"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"reflect"
	"testing"
)

// 验证数据库仅传本页，而非把全部原文交给应用切片；同时检查跨页没有重排或改写。
func TestTaskCaseSQLPaginationFullSequence(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	r := taskSample()
	c := &r.TaskReview.Cases[0]
	c.ContextStatus = "source_incomplete"
	for i := len(c.Records); i < 123; i++ {
		text := fmt.Sprintf("第%d段：引号\"、反斜线\\、换行\n中文😀 <原文> covered_child_keys", i)
		hash := sha256.Sum256([]byte(text))
		c.Records = append(c.Records, TaskRecord{ID: fmt.Sprintf("event:page:%d", i), SourceHash: c.Records[1].SourceHash, TextHash: hex.EncodeToString(hash[:]), Role: "assistant", Origin: "assistant_output", Text: text, Limitations: []string{"来源未记录时间，不能推定本周表现"}})
	}
	c.RecordCount = len(c.Records)
	if err := r.Validate(); err != nil {
		t.Fatal(err)
	}
	original, err := json.Marshal(r)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.Import(ctx, Bundle{SchemaVersion: 1, SourceName: "pagination-independent", Reports: []Result{r}}, 1); err != nil {
		t.Fatal(err)
	}
	rows, total, err := s.List(ctx, Filter{Page: 1, PageSize: 20})
	if err != nil || total != 1 || len(rows) != 1 {
		t.Fatal("未找到合成报告", err)
	}
	id := rows[0].ID
	// 使用非零偏移与不同 owner，防止新 $3/$4 分页参数意外取代 $5 权限。
	if _, err = s.GetTaskCase(ctx, id, 2, c.ID, 2, 50); !errors.Is(err, ErrNotFound) {
		t.Fatal("草稿第二页泄露", err)
	}
	if _, err = s.Publish(ctx, id, rows[0].Revision, 1); err != nil {
		t.Fatal(err)
	}
	detailBefore, err := s.Get(ctx, id, 2)
	if err != nil {
		t.Fatal(err)
	}
	var all []TaskRecord
	for page := 1; page <= 3; page++ {
		got, e := s.GetTaskCase(ctx, id, 2, c.ID, page, 50)
		if e != nil {
			t.Fatal(e)
		}
		count := 50
		if page == 3 {
			count = 23
		}
		if got.Total != 123 || got.RecordCount != 123 || got.Page != page || got.PageSize != 50 || len(got.Records) != count {
			t.Fatalf("第%d页计数错误：%+v", page, got)
		}
		metadata := got.TaskCase
		want := *c
		metadata.Records = nil
		want.Records = nil
		if !reflect.DeepEqual(metadata, want) {
			t.Fatal("分页改写案例元数据")
		}
		all = append(all, got.Records...)
	}
	if !reflect.DeepEqual(all, c.Records) {
		t.Fatal("跨页序列、特殊字符或未知时间发生变化")
	}
	empty, err := s.GetTaskCase(ctx, id, 2, c.ID, 4, 50)
	if err != nil || empty.Total != 123 || empty.Records == nil || len(empty.Records) != 0 {
		t.Fatal("越界末页应保留总数及空数组", err)
	}
	// 直接检查 SQL 的三列合同，避免仅靠 API 结果掩盖全量数据库传输。
	var meta, pageRaw []byte
	var sqlTotal int
	err = s.db.QueryRowContext(ctx, taskCasePageQuery, id, c.ID, 50, 100).Scan(&meta, &sqlTotal, &pageRaw)
	if err != nil {
		t.Fatal(err)
	}
	var fields map[string]json.RawMessage
	var sqlRecords []TaskRecord
	if err = json.Unmarshal(meta, &fields); err != nil {
		t.Fatal(err)
	}
	if _, exists := fields["records"]; exists {
		t.Fatal("元数据仍含全量原文")
	}
	if err = json.Unmarshal(pageRaw, &sqlRecords); err != nil {
		t.Fatal(err)
	}
	if sqlTotal != 123 || !reflect.DeepEqual(sqlRecords, c.Records[50:100]) {
		t.Fatal("SQL没有仅返回第二页")
	}
	for _, owner := range []int64{3, 50, 100} {
		if _, err = s.GetTaskCase(ctx, id, owner, c.ID, 2, 50); !errors.Is(err, ErrNotFound) {
			t.Fatalf("owner=%d绕过第二页权限：%v", owner, err)
		}
	}
	if _, err = s.db.ExecContext(ctx, `INSERT INTO audit_whitelist_users(user_id) VALUES(2)`); err != nil {
		t.Fatal(err)
	}
	if _, err = s.GetTaskCase(ctx, id, 2, c.ID, 2, 50); !errors.Is(err, ErrTaskCaseForbidden) {
		t.Fatal("白名单第二页泄露", err)
	}
	if _, err = s.db.ExecContext(ctx, `DELETE FROM audit_whitelist_users WHERE user_id=2`); err != nil {
		t.Fatal(err)
	}
	detailAfter, err := s.Get(ctx, id, 2)
	if err != nil || !reflect.DeepEqual(detailBefore, detailAfter) || !reflect.DeepEqual(detailAfter.Score, r.Score) {
		t.Fatal("分页改变原报告或评分", err)
	}
	after, _ := json.Marshal(r)
	if !bytes.Equal(original, after) {
		t.Fatal("分页修改调用方原始对象")
	}
	// 发布新修订后，旧修订的第二页也必须不可见；管理员仍可追溯。
	r.TaskReview.Cases[0].Outcome = "新修订仍保留失败与未决"
	if _, err = s.Import(ctx, Bundle{SchemaVersion: 1, SourceName: "pagination-independent", Reports: []Result{r}}, 1); err != nil {
		t.Fatal(err)
	}
	rows, _, err = s.List(ctx, Filter{Page: 1, PageSize: 20})
	if err != nil {
		t.Fatal(err)
	}
	var nextID int64
	var revision int
	for _, row := range rows {
		if row.ID != id {
			nextID = row.ID
			revision = row.Revision
		}
	}
	if nextID == 0 {
		t.Fatal("缺少新修订")
	}
	if _, err = s.Publish(ctx, nextID, revision, 1); err != nil {
		t.Fatal(err)
	}
	if _, err = s.GetTaskCase(ctx, id, 2, c.ID, 2, 50); !errors.Is(err, ErrNotFound) {
		t.Fatal("旧修订第二页泄露", err)
	}
	if _, err = s.GetTaskCase(ctx, id, 0, c.ID, 2, 50); err != nil {
		t.Fatal("管理员不能追溯旧页", err)
	}
	// SQL 本身也应拒绝不存在的案例，不合成空的元数据。
	if err = s.db.QueryRowContext(ctx, taskCasePageQuery, id, fmt.Sprintf("%064x", 42), 0, 50).Scan(&meta, &sqlTotal, &pageRaw); !errors.Is(err, sql.ErrNoRows) {
		t.Fatal("不存在案例被合成", err)
	}
}
