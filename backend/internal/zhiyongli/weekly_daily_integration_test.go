//go:build integration

package zhiyongli

import (
	"context"
	"encoding/json"
	"fmt"
	"testing"

	"github.com/Wei-Shaw/sub2api/migrations"
)

func TestWeeklyDailyPublishedSourcesPrivacyAndChanges(t *testing.T) {
	s := testStore(t)
	raw, err := migrations.FS.ReadFile("248_zhiyongli_daily_reports.sql")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.db.Exec(string(raw)); err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	w := weeklyDailyFixture(t)
	w.UserEmail = "person@example.test"
	ids := map[string]int64{}
	for i, day := range w.WorkdayScope.WorkingDates {
		d := dailyFixture()
		d.UserEmail = w.UserEmail
		if day != "2026-11-18" {
			d.PeriodStart = day
			d.WorkdayScope, _ = dailyWindow(day)
			d.PeriodEnd = d.WorkdayScope.PeriodEnd
			d.DailyReview.WindowStart = day + "T08:00:00+08:00"
			d.DailyReview.WindowEnd = day + "T18:00:00+08:00"
			d.DailyReview.Conversations = map[string]DailyTask{}
			d.DailyReview.Behaviors = map[string]DailyBehavior{}
			d.DailyReview.SourceLabels = map[string]string{}
			d.DailyReview.SourceSessions = map[string]string{}
			d.Items = nil
			d.ScoreStatus, d.ScoreReason = "no_activity", "当日没有可评分记录。"
		}
		run, err := s.Import(ctx, Bundle{SchemaVersion: 1, SourceName: fmt.Sprintf("day-%d", i), Reports: []Result{d}}, 1)
		if err != nil {
			t.Fatal(err)
		}
		var id int64
		if err = s.db.QueryRow(`SELECT id FROM zhiyongli_reports WHERE run_id=$1`, run.ID).Scan(&id); err != nil {
			t.Fatal(err)
		}
		if _, err = s.Publish(ctx, id, 1, 1); err != nil {
			t.Fatal(err)
		}
		w.WeeklyReview.Sources[i].ReportID = id
		ids[day] = id
	}
	bundle := Bundle{SchemaVersion: 1, SourceName: "weekly-from-days", Reports: []Result{w}}
	run, err := s.Import(ctx, bundle, 1)
	if err != nil {
		t.Fatal(err)
	}
	rows, total, err := s.List(ctx, Filter{PeriodType: "week", Page: 1, PageSize: 20})
	if err != nil || total != 1 || rows[0].WeeklyReview != nil || rows[0].Items != nil {
		t.Fatal("列表不能泄露周评原文", err)
	}
	id := rows[0].ID
	if _, err = s.Get(ctx, id, 2); err == nil {
		t.Fatal("本人不能读取草稿")
	}
	if _, err = s.Publish(ctx, id, 1, 1); err != nil {
		t.Fatal(err)
	}
	got, err := s.Get(ctx, id, 2)
	if err != nil || got.WeeklyReview == nil || *got.Score != "82.0" {
		t.Fatal("本人应能读到已发布周评", err)
	}
	if _, err = s.Get(ctx, id, 3); err == nil {
		t.Fatal("不能读取他人周评原文")
	}
	again, err := s.Import(ctx, bundle, 1)
	if err != nil || again.ID != run.ID {
		t.Fatal("同包恢复须幂等", err)
	}
	// 新周评允许从同一批五日原话重新形成行为判断，不继承日评的主归及主张。
	copyRaw, _ := json.Marshal(w)
	var conversation Result
	if err = json.Unmarshal(copyRaw, &conversation); err != nil {
		t.Fatal(err)
	}
	for key, behavior := range conversation.WeeklyReview.Behaviors {
		delete(conversation.WeeklyReview.Behaviors, key)
		newKey := "new-" + key
		behavior.Claim = "根据同一份五日原文重新形成的具体行为判断"
		conversation.WeeklyReview.Behaviors[newKey] = behavior
		for i := range conversation.Items {
			for j := range conversation.Items[i].Evidence {
				if conversation.Items[i].Evidence[j].ID == key {
					conversation.Items[i].Evidence[j].ID = newKey
				}
			}
		}
		break
	}
	if _, err = s.Import(ctx, Bundle{SchemaVersion: 1, SourceName: "legacy-cannot-rejudge", Reports: []Result{conversation}}, 1); err == nil {
		t.Fatal("旧版不能放宽为重新识别行为")
	}
	conversation.WeeklyReview.Version, conversation.PolicyVersions.Review = conversationWeeklyVersion, conversationWeeklyVersion
	if _, err = s.Import(ctx, Bundle{SchemaVersion: 1, SourceName: "conversation-can-rejudge", Reports: []Result{conversation}}, 1); err != nil {
		t.Fatal("新版应允许从相同原文重新判断", err)
	}
	changedTask := conversation.WeeklyReview.Conversations["d20261118-task-1"]
	changedTask.Goal = "替换原日评的任务目标"
	conversation.WeeklyReview.Conversations["d20261118-task-1"] = changedTask
	if _, err = s.Import(ctx, Bundle{SchemaVersion: 1, SourceName: "conversation-cannot-change-raw", Reports: []Result{conversation}}, 1); err == nil {
		t.Fatal("新版也不能替换五日已发布原文与任务")
	}
	// 日评后续修订替换后，旧来源周评不能再导入或发布。
	if _, err = s.db.Exec(`UPDATE zhiyongli_reports SET publication_status='superseded' WHERE id=$1`, ids["2026-11-18"]); err != nil {
		t.Fatal(err)
	}
	bundle.SourceName = "stale-week-source"
	if _, err = s.Import(ctx, bundle, 1); err == nil {
		t.Fatal("应拒绝来源已变的周评")
	}
	if _, err = s.Publish(ctx, id, 1, 1); err == nil {
		t.Fatal("发布检查必须拦截已变的日评来源")
	}
	// 只修改周评原文即使同步修改正文哈希也不能绕过来源比对。
	if _, err = s.db.Exec(`UPDATE zhiyongli_reports SET publication_status='published' WHERE id=$1`, ids["2026-11-18"]); err != nil {
		t.Fatal(err)
	}
	w = weeklyDailyFixture(t)
	w.UserEmail = "person@example.test"
	for i, day := range w.WorkdayScope.WorkingDates {
		w.WeeklyReview.Sources[i].ReportID = ids[day]
	}
	task := w.WeeklyReview.Conversations["d20261118-task-1"]
	task.Goal = "未经日评确认的任务目标"
	w.WeeklyReview.Conversations["d20261118-task-1"] = task
	if _, err = s.Import(ctx, Bundle{SchemaVersion: 1, SourceName: "tampered-task", Reports: []Result{w}}, 1); err == nil {
		t.Fatal("周评不能篡改已核任务")
	}
}
