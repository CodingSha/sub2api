package zhiyongli

import (
	"strings"
	"testing"
	"time"
)

func weeklyDailyFixture(t *testing.T) Result {
	t.Helper()
	r := dailyFixture()
	w := &WeeklyReview{DailyReview: *r.DailyReview}
	r.DailyReview = nil
	r.WeeklyReview = w
	r.PeriodType, r.PeriodStart, r.PeriodEnd = "week", "2026-11-16", "2026-11-23"
	var err error
	r.WorkdayScope, err = workdayWindow(r.PeriodStart, r.PeriodEnd, "")
	if err != nil {
		t.Fatal(err)
	}
	w.Version, w.WindowStart, w.WindowEnd = WeeklyDailyVersion, "2026-11-16T08:00:00+08:00", "2026-11-20T18:00:00+08:00"
	r.PolicyVersions.Review, r.PolicyVersions.Aggregation, r.PolicyVersions.Scope = WeeklyDailyVersion, WeeklyDailyAggregation, WorkdayScopeVersion
	for i, day := range r.WorkdayScope.WorkingDates {
		w.Sources = append(w.Sources, DailyReportSource{int64(i + 1), day, strings.Repeat("c", 64), strings.Repeat("a", 64)})
	}
	p := weeklyPrefix("2026-11-18")
	task := w.Conversations["task-1"]
	for i := range task.Records {
		task.Records[i].ID = p + task.Records[i].ID
	}
	w.Conversations = map[string]DailyTask{p + "task-1": task}
	w.SourceLabels, w.SourceSessions = map[string]string{p + "u": task.Title}, map[string]string{p + "u": p + "task-1"}
	b := map[string]DailyBehavior{}
	for id, row := range w.Behaviors {
		row.SourceRef = p + row.SourceRef
		b[p+id] = row
	}
	w.Behaviors = b
	for i := range r.Items {
		for j := range r.Items[i].Evidence {
			r.Items[i].Evidence[j].ID = p + r.Items[i].Evidence[j].ID
			r.Items[i].Evidence[j].SourceRef = p + r.Items[i].Evidence[j].SourceRef
		}
	}
	return r
}

func TestWeeklyDailyEvidenceScoresAndScope(t *testing.T) {
	r := weeklyDailyFixture(t)
	fake := "99.9"
	r.Score = &fake
	if err := r.Validate(); err != nil {
		t.Fatal(err)
	}
	if *r.Score != "82.0" || r.Rating != nil || len(r.WeeklyReview.Sources) != 5 {
		t.Fatal("周分必须由原文分项重算且保留五日来源", r.Score)
	}
	tests := map[string]func(*Result){
		"遗漏一天": func(r *Result) { r.WeeklyReview.Sources = r.WeeklyReview.Sources[:4] },
		"重复来源": func(r *Result) { r.WeeklyReview.Sources[1] = r.WeeklyReview.Sources[0] },
		"伪装日评": func(r *Result) { r.PeriodType = "day" },
		"旧汇总":  func(r *Result) { r.PolicyVersions.Aggregation = DailyAggregationVersion },
		"替换正文": func(r *Result) {
			task := r.WeeklyReview.Conversations["d20261118-task-1"]
			task.Records[0].Text = "替换的原话"
			r.WeeklyReview.Conversations["d20261118-task-1"] = task
		},
		"周末计分": func(r *Result) {
			r.Items[0].Evidence[0].OccurredAt, _ = time.Parse(time.RFC3339, "2026-11-21T09:00:00+08:00")
		},
		"窗口外计分": func(r *Result) {
			r.Items[0].Evidence[0].OccurredAt, _ = time.Parse(time.RFC3339, "2026-11-18T18:00:00+08:00")
		},
	}
	for name, change := range tests {
		t.Run(name, func(t *testing.T) {
			r := weeklyDailyFixture(t)
			change(&r)
			if r.Validate() == nil {
				t.Fatal("应拒绝无效周评")
			}
		})
	}
}

func TestWeeklyDailyMissingDimensionKeepsAnalysisWithoutTotal(t *testing.T) {
	r := weeklyDailyFixture(t)
	for i := range r.Items {
		if r.Items[i].Code[:1] != "P" {
			r.Items[i].State = "unobservable"
			r.Items[i].Exact, r.Items[i].Score = nil, nil
			r.Items[i].Evidence = nil
		}
	}
	// 即使输入伪造了总分，导入端也按分项重算，不能发布单维度的周总分。
	r.Score, r.Exact = ptr("90.0"), ptr("90")
	if err := r.Validate(); err != nil {
		t.Fatal(err)
	}
	if r.Score != nil || r.Exact != nil || r.ScoreStatus != "observation_only" || r.Dimensions[0].Score == nil || *r.Dimensions[0].Score != "90.0" || r.Dimensions[1].Score != nil {
		t.Fatal("缺维度须保留分析且不生成综合分", r)
	}
}
