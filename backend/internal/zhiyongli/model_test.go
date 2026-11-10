package zhiyongli

import (
	"strings"
	"testing"
	"time"
)

func ptr(s string) *string { return &s }
func sampleResult() Result {
	r := Result{UserEmail: "person@example.test", PeriodType: "week", PeriodStart: "2026-09-07", PeriodEnd: "2026-09-14", ScoreStatus: "scored", SourceDigest: strings.Repeat("a", 64), PolicyVersions: Versions{Scale: ScaleVersion, Aggregation: ScaleVersion, Rating: "G2-proposal28", Rules: "1.2"}}
	at, _ := time.Parse(time.RFC3339, "2026-09-08T09:00:00+08:00")
	for _, d := range []string{"P", "M", "J", "E"} {
		for _, n := range []string{"1", "2", "3"} {
			code := d + n
			r.Items = append(r.Items, Item{Code: code, State: "observed", Exact: ptr("80"), Reason: "已按有效要求完成必要安排", Criterion: "处理符合任务前提", Evidence: []Evidence{{ID: "ref1", SourceRef: "event:123", SourceHash: strings.Repeat("b", 64), TaskTitle: "验收用例", OccurredAt: at, Role: "user", Origin: "direct_expression", Quote: "先核对接口再改字段", Context: "先核对接口再改字段", Supports: "明确字段依据"}}})
		}
	}
	return r
}
func TestExactScoreAndMissingDimensions(t *testing.T) {
	r := sampleResult()
	for i := range r.Items {
		if r.Items[i].Code[:1] != "E" {
			r.Items[i].Exact = ptr("90")
		}
	}
	if e := r.Validate(); e != nil {
		t.Fatal(e)
	}
	if *r.Score != "88.5" || *r.Exact != "177/2" || r.Rating != nil {
		t.Fatalf("unexpected total: %+v", r)
	}
	for i := range r.Items {
		if r.Items[i].Code[:1] == "E" {
			r.Items[i].State = "no_opportunity"
			r.Items[i].Exact = nil
		}
	}
	r.ScoreReason = "使用效率本期无处理机会"
	if e := r.Validate(); e != nil {
		t.Fatal(e)
	}
	if r.Score != nil || r.Dimensions[3].Score != nil {
		t.Fatal("missing dimension must not become zero")
	}
}
func TestNecessaryGapAndInputValidation(t *testing.T) {
	r := sampleResult()
	r.Items[0].Exact = ptr("20")
	r.Items[0].NecessaryGap = true
	if e := r.Validate(); e != nil {
		t.Fatal(e)
	}
	if *r.Dimensions[0].Score != "20.0" || *r.Score != "68.0" {
		t.Fatal("gap was averaged away")
	}
	cases := []func(*Result){func(r *Result) { r.Items[0].Evidence = nil }, func(r *Result) { r.Items[1].Code = "P1" }, func(r *Result) { r.Items[0].Evidence[0].Context = "不同原文" }, func(r *Result) { r.Items[0].Exact = ptr("81") }, func(r *Result) { r.PolicyVersions.Scale = "unknown" }, func(r *Result) { r.PeriodEnd = "2026-09-13" }}
	for i, change := range cases {
		r := sampleResult()
		change(&r)
		if e := r.Validate(); e == nil {
			t.Fatalf("case %d accepted invalid input", i)
		}
	}
}
func monthResult() Result {
	r := sampleResult()
	r.PeriodType = "month"
	r.PeriodStart = "2026-09-01"
	r.PeriodEnd = "2026-10-01"
	r.PolicyVersions.Aggregation = MonthVersion
	dates := []string{"2026-09-01", "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28", "2026-10-01"}
	for j := 0; j < 5; j++ {
		r.Coverage = append(r.Coverage, Coverage{dates[j], dates[j+1], true})
	}
	for i := range r.Items {
		r.Items[i].Exact = ptr("80")
		r.Items[i].PeriodStates = map[string]string{}
		r.Items[i].Evidence = nil
		for j := 0; j < 5; j++ {
			r.Items[i].PeriodStates[dates[j]] = "observed"
			r.Items[i].Components = append(r.Items[i].Components, Component{PeriodStart: dates[j], PeriodEnd: dates[j+1], Exact: "80"})
			ev := sampleResult().Items[i].Evidence[0]
			ev.ID = dates[j]
			ev.OccurredAt, _ = time.Parse(time.RFC3339, dates[j]+"T09:00:00+08:00")
			r.Items[i].Evidence = append(r.Items[i].Evidence, ev)
		}
	}
	return r
}

func TestEvidenceAndNecessaryGapCannotBeBypassed(t *testing.T) {
	cases := []struct {
		name   string
		make   func() Result
		change func(*Result)
	}{
		{"周低分未标缺口", sampleResult, func(r *Result) { r.Items[0].Exact = ptr("40") }},
		{"月低分未标缺口", monthResult, func(r *Result) { r.Items[0].Components[0].Exact = "40"; r.Items[0].Exact = ptr("72") }},
		{"仅背景证据", sampleResult, func(r *Result) { r.Items[0].Evidence[0].Origin = "context" }},
		{"任务标题未脱敏", sampleResult, func(r *Result) { r.Items[0].Evidence[0].TaskTitle = "password=testingCredential123456" }},
		{"历史本人证据", sampleResult, func(r *Result) {
			r.Items[0].Evidence[0].OccurredAt = r.Items[0].Evidence[0].OccurredAt.AddDate(0, 0, -7)
		}},
		{"月期间缺本人证据", monthResult, func(r *Result) { r.Items[0].Evidence = r.Items[0].Evidence[1:] }},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			r := c.make()
			c.change(&r)
			if err := r.Validate(); err == nil {
				t.Fatal("无效证据或缺口标记被接受")
			}
		})
	}
	r := monthResult()
	r.Items[0].Components[0].Exact = "20"
	r.Items[0].Components[0].NecessaryGap = true
	r.Items[0].Exact = ptr("68")
	r.GapDispositions = []GapDisposition{{ItemCode: "P1", PeriodStart: "2026-09-01", Status: "recovered", Reason: "后续期间已恢复", RecoveryPeriod: "2026-09-07", RecoveryEvidenceIDs: []string{"2026-09-07"}}}
	if err := r.Validate(); err != nil {
		t.Fatal(err)
	}
	r.GapDispositions[0].RecoveryEvidenceIDs = []string{"2026-09-21"}
	if err := r.Validate(); err == nil {
		t.Fatal("恢复期间之外的证据被接受")
	}
}
func TestMonthlyWindowsAndUnroundedRating(t *testing.T) {
	r := monthResult()
	if e := r.Validate(); e != nil {
		t.Fatal(e)
	}
	if *r.Score != "80.0" || *r.Rating != "L4" {
		t.Fatal("bad monthly rating")
	}
	r.Items[0].Components[0].PeriodEnd = "2026-09-08"
	if e := r.Validate(); e == nil {
		t.Fatal("overlap accepted")
	}
	r = monthResult()
	r.Items[0].Components[0].Exact = "20"
	r.Items[0].Components[0].NecessaryGap = true
	r.Items[0].Exact = ptr("68")
	if e := r.Validate(); e == nil {
		t.Fatal("undisposed gap accepted")
	}
	r.GapDispositions = []GapDisposition{{ItemCode: "P1", PeriodStart: "2026-09-01", Status: "open", Reason: "截至月末未处理"}}
	if e := r.Validate(); e != nil {
		t.Fatal(e)
	}
	if *r.Dimensions[0].Score != "20.0" {
		t.Fatal("monthly gap did not cap dimension")
	}
	n, _ := rational("17999/200")
	if *display(n) != "90.0" || *level(n) != "L4" {
		t.Fatal("rounding changed rating")
	}
	n, _ = rational("309/4")
	if *display(n) != "77.3" {
		t.Fatal("half-up failed")
	}
}
