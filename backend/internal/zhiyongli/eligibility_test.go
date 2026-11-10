package zhiyongli

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"
)

func withObservation(r Result, tasks int, days []string) Result {
	r.PolicyVersions.Rules = "1.3"
	r.Eligibility = &Eligibility{Version: EligibilityVersion, SourcesComplete: true, DimensionRefs: map[string][]string{}}
	if r.Assessment != nil {
		r.Assessment.Version = ObservationAssessmentVersion
	}
	for i := 0; i < tasks || i < len(days); i++ {
		at, _ := time.Parse(time.RFC3339, days[i%len(days)]+"T09:00:00+08:00")
		r.Eligibility.Observations = append(r.Eligibility.Observations, Observation{fmt.Sprintf("event:%d", i), strings.Repeat("b", 64), at, []string{fmt.Sprintf("task:%d", i%tasks)}})
	}
	for _, d := range []string{"P", "M", "J", "E"} {
		r.Eligibility.DimensionRefs[d] = []string{}
		for _, e := range r.Eligibility.Observations {
			r.Eligibility.DimensionRefs[d] = append(r.Eligibility.DimensionRefs[d], e.SourceRef)
			ev := sampleResult().Items[0].Evidence[0]
			ev.ID, ev.SourceRef, ev.OccurredAt = e.SourceRef, e.SourceRef, e.OccurredAt
			for i := range r.Items {
				if r.Items[i].Code == d+"1" {
					r.Items[i].Evidence = append(r.Items[i].Evidence, ev)
				}
			}
		}
	}
	r.Eligibility.SourceDigest = observationDigest(r.Eligibility)
	return r
}

func TestObservationThresholdsAndLongTask(t *testing.T) {
	for _, tc := range []struct {
		name  string
		tasks int
		days  []string
		want  bool
		route string
	}{
		{"五任务三日", 5, []string{"2026-09-07", "2026-09-08", "2026-09-09"}, true, "standard"},
		{"百任务两日", 100, []string{"2026-09-07", "2026-09-08"}, false, "standard"},
		{"单任务跨三日", 1, []string{"2026-09-07", "2026-09-08", "2026-09-09"}, true, "long_task"},
		{"两任务仅两日", 2, []string{"2026-09-07", "2026-09-08"}, false, "insufficient_task_coverage"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := withObservation(sampleResult(), tc.tasks, tc.days)
			if e := r.Validate(); e != nil {
				t.Fatal(e)
			}
			if r.Eligibility.Eligible != tc.want || r.Eligibility.Route != tc.route {
				t.Fatal(r.Eligibility)
			}
			if (r.Score != nil) != tc.want || *r.Dimensions[0].Score != "80.0" {
				t.Fatal("资格不能改变维度表现")
			}
			if e := r.Validate(); e != nil {
				t.Fatal("持久化后再次核对失败", e)
			}
		})
	}
}

func TestObservationCannotTrustCountsOrRepeatedEvidence(t *testing.T) {
	r := withObservation(sampleResult(), 5, []string{"2026-09-07", "2026-09-08", "2026-09-09"})
	r.Eligibility.DimensionRefs["E"] = r.Eligibility.DimensionRefs["E"][:1]
	r.Eligibility.SourceDigest = observationDigest(r.Eligibility)
	r.Eligibility.Eligible, r.Eligibility.ActiveDays, r.Eligibility.TaskCount = true, 7, 100
	if e := r.Validate(); e != nil {
		t.Fatal(e)
	}
	if r.Eligibility.Eligible || r.Score != nil || r.Eligibility.TaskCount != 5 || r.ScoreStatus != "observation_only" {
		t.Fatal("单条E被虚报数量绕过")
	}
	for _, change := range []func(*Result){
		func(r *Result) {
			r.Eligibility.Observations = append(r.Eligibility.Observations, r.Eligibility.Observations[0])
		},
		func(r *Result) {
			r.Eligibility.DimensionRefs["E"] = append(r.Eligibility.DimensionRefs["E"], "fake:source")
		},
		func(r *Result) { r.Eligibility.Observations[0].SourceHash = strings.Repeat("c", 64) },
		func(r *Result) { r.Eligibility.SourceDigest = strings.Repeat("c", 64) },
	} {
		r := withObservation(sampleResult(), 5, []string{"2026-09-07", "2026-09-08", "2026-09-09"})
		change(&r)
		if r.Validate() == nil {
			t.Fatal("重复、伪造或签名变化被接受")
		}
	}
}

func TestNewMonthlyLocalUnknownAndLegacyIsolation(t *testing.T) {
	r := monthResult()
	r.Assessment = reviewedScope()
	r.Items[1].PeriodStates["2026-09-28"] = "unobservable"
	r.Items[1].Components = r.Items[1].Components[:4]
	r.Items[1].Evidence = r.Items[1].Evidence[:4]
	if e := r.Validate(); e != nil || r.Rating != nil {
		t.Fatal("旧版含未知的月级语义改变", e)
	}
	days := []string{"2026-09-01", "2026-09-02", "2026-09-03", "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-21"}
	r = withObservation(r, 20, days)
	if e := r.Validate(); e != nil {
		t.Fatal(e)
	}
	if r.Rating == nil || *r.Rating != "L4" || *r.Score != "80.0" {
		t.Fatal("局部未知错误否决充分整月")
	}
	r.Items[1].PeriodStates["2026-09-28"] = "attribution_unclear"
	if e := r.Validate(); e != nil || r.Rating != nil {
		t.Fatal("关键归属未决不能豁免", e)
	}
	r.Items[1].PeriodStates["2026-09-28"] = "unobservable"
	r.Eligibility.SourcesComplete = false
	if e := r.Validate(); e != nil || r.Score != nil || r.ScoreStatus != "data_pending" {
		t.Fatal("未完成来源不能发布综合分", e)
	}
}

func TestLowPerformanceDoesNotLoseEligibility(t *testing.T) {
	r := withObservation(sampleResult(), 5, []string{"2026-09-07", "2026-09-08", "2026-09-09"})
	for i := range r.Items {
		r.Items[i].Exact = ptr("60")
	}
	if e := r.Validate(); e != nil || r.Score == nil || *r.Score != "60.0" || !r.Eligibility.Eligible {
		t.Fatal("低表现不能隐藏为观察不足", e)
	}
}

func TestMonthLongTaskObservation(t *testing.T) {
	days := []string{"2026-09-01", "2026-09-02", "2026-09-03", "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-21"}
	r := withObservation(monthResult(), 1, days)
	if e := r.Validate(); e != nil || !r.Eligibility.Eligible || r.Eligibility.Route != "long_task" || r.Score == nil {
		t.Fatal("持续任务满足跨日跨周观察时应可出月分", e)
	}
	r.Eligibility.DimensionRefs["E"] = r.Eligibility.DimensionRefs["E"][:3]
	r.Eligibility.SourceDigest = observationDigest(r.Eligibility)
	if e := r.Validate(); e != nil || r.Eligibility.Eligible || r.Score != nil || r.ScoreStatus != "observation_only" {
		t.Fatal("持续任务不能豁免分维跨周观察", e)
	}
}

func TestEmptyObservationReportStates(t *testing.T) {
	for _, tc := range []struct {
		status   string
		complete bool
		valid    bool
	}{
		{"no_activity", true, true},
		{"data_pending", false, true},
		{"no_activity", false, false},
		{"data_pending", true, false},
	} {
		t.Run(fmt.Sprintf("%s/%t", tc.status, tc.complete), func(t *testing.T) {
			r := sampleResult()
			r.PolicyVersions.Rules = "1.3"
			r.Items, r.ScoreStatus, r.ScoreReason = nil, tc.status, "本期来源观察状态"
			r.Eligibility = &Eligibility{Version: EligibilityVersion, SourcesComplete: tc.complete,
				Observations: []Observation{}, DimensionRefs: map[string][]string{"P": {}, "M": {}, "J": {}, "E": {}}}
			r.Eligibility.SourceDigest = observationDigest(r.Eligibility)
			err := r.Validate()
			if (err == nil) != tc.valid || r.Score != nil || r.Rating != nil {
				t.Fatal("系统未完成与无记录必须区分，且不补零分", err)
			}
		})
	}
}

func TestRealEligibilityBundle(t *testing.T) {
	path := os.Getenv("ZHIYONGLI_ELIGIBILITY_BUNDLE")
	if path == "" {
		t.Skip("须显式提供已核真实结果包")
	}
	raw, e := os.ReadFile(path)
	if e != nil {
		t.Fatal(e)
	}
	var bundle Bundle
	if e = json.Unmarshal(raw, &bundle); e != nil {
		t.Fatal(e)
	}
	want := map[string]string{"lican": "80.3", "liuwb": "81.8", "liyu": "84.1", "shasp": "81.6", "tangjing": "83.7"}
	for _, r := range bundle.Reports {
		if e := r.Validate(); e != nil {
			t.Fatal(r.UserEmail, r.PeriodStart, e)
		}
		if r.PeriodType == "month" {
			user := strings.Split(r.UserEmail, "@")[0]
			if r.Score == nil || *r.Score != want[user] || r.Rating == nil || *r.Rating != "L4" {
				t.Fatal(user, "月度分数或评级不符")
			}
		}
	}
}

func TestCurrentLocalBehaviorDoesNotInventTasks(t *testing.T) {
	r := withObservation(sampleResult(), 1, []string{"2026-09-07"})
	r.PolicyVersions.Rules = RulesVersion
	r.PolicyVersions.Review = ReviewPolicyVersion
	r.Eligibility.Version = CurrentEligibilityVersion
	for i := range r.Eligibility.Observations {
		r.Eligibility.Observations[i].TaskKeys = []string{}
	}
	r.Eligibility.SourceDigest = observationDigest(r.Eligibility)
	if err := r.Validate(); err != nil {
		t.Fatal(err)
	}
	if r.Eligibility.TaskCount != 0 || len(r.Eligibility.Observations) != 1 || r.Score == nil {
		t.Fatal("局部行为应保留本人依据，不虚构任务，也不设置任务门槛")
	}
}
