package zhiyongli

import (
	"encoding/json"
	"fmt"
	"os"
	"testing"
	"time"
)

func TestQualifiedRealBundle(t *testing.T) {
	path := os.Getenv("ZHIYONGLI_QUALIFIED_BUNDLE")
	if path == "" {
		t.Skip("显式提供新资格真实回放包")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var b Bundle
	if err = json.Unmarshal(raw, &b); err != nil {
		t.Fatal(err)
	}
	for _, r := range b.Reports {
		wantScore, wantStatus := r.Score, r.ScoreStatus
		if err = r.Validate(); err != nil {
			t.Fatal(r.UserEmail, r.PeriodStart, err)
		}
		if r.ScoreStatus != wantStatus || (r.Score == nil) != (wantScore == nil) || r.Score != nil && *r.Score != *wantScore {
			t.Fatal("Python/Go不一致", r.UserEmail, r.PeriodStart)
		}
		if err = r.Validate(); err != nil {
			t.Fatal("重验", err)
		}
		t.Log(r.UserEmail, r.PeriodStart, r.ScoreStatus, r.Eligibility.ValidWeeks, r.Score)
	}
}

func qualifiedWeek(start string, tasks int) Result {
	r := sampleResult()
	a, _ := date(start)
	r.PeriodStart, r.PeriodEnd = start, a.AddDate(0, 0, 7).Format("2006-01-02")
	r = withObservation(r, tasks, []string{start})
	r.PolicyVersions.Rules = "1.4"
	r.Eligibility.Version = QualifiedEligibilityVersion
	for i := range r.Eligibility.Observations {
		r.Eligibility.Observations[i].SourceRef += ":" + start
	}
	for d, refs := range r.Eligibility.DimensionRefs {
		for i := range refs {
			refs[i] += ":" + start
		}
		r.Eligibility.DimensionRefs[d] = refs
	}
	for i := range r.Items {
		r.Items[i].Evidence = r.Items[i].Evidence[1:]
		for j := range r.Items[i].Evidence {
			ev := &r.Items[i].Evidence[j]
			ev.ID += ":" + start
			ev.SourceRef += ":" + start
		}
		if len(r.Items[i].Evidence) == 0 {
			r.Items[i].Evidence = append([]Evidence{}, r.Items[i-1].Evidence...)
		}
	}
	r.Eligibility.SourceDigest = observationDigest(r.Eligibility)
	return r
}

func qualifiedMonth(count int) Result {
	r := monthResult()
	r.PolicyVersions.Rules, r.PolicyVersions.Aggregation = "1.4", QualifiedMonthVersion
	r.Eligibility = &Eligibility{Version: QualifiedEligibilityVersion, SourcesComplete: true, DimensionRefs: map[string][]string{"P": {}, "M": {}, "J": {}, "E": {}}}
	starts := []string{"2026-08-31", "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28"}
	for j, start := range starts {
		n := 19
		if j < count {
			n = 20
		}
		w := qualifiedWeek(start, n)
		// 跨月首周使用本月内行为，月外行为仍不进入月来源。
		if j == 0 {
			for k := range w.Eligibility.Observations {
				w.Eligibility.Observations[k].OccurredAt = w.Eligibility.Observations[k].OccurredAt.AddDate(0, 0, 1)
			}
			for k := range w.Items {
				for l := range w.Items[k].Evidence {
					w.Items[k].Evidence[l].OccurredAt = w.Items[k].Evidence[l].OccurredAt.AddDate(0, 0, 1)
				}
			}
			w.Eligibility.SourceDigest = observationDigest(w.Eligibility)
		}
		r.WeeklyReports = append(r.WeeklyReports, w)
		r.Eligibility.Observations = append(r.Eligibility.Observations, w.Eligibility.Observations...)
	}
	for i := range r.Items {
		it := &r.Items[i]
		it.Components, it.Evidence = nil, nil
		for j, c := range r.Coverage {
			it.PeriodStates[c.PeriodStart] = "excluded_week"
			if j >= count {
				continue
			}
			it.PeriodStates[c.PeriodStart] = "observed"
			it.Components = append(it.Components, Component{PeriodStart: c.PeriodStart, PeriodEnd: c.PeriodEnd, Exact: "80"})
			it.Evidence = append(it.Evidence, r.WeeklyReports[j].Items[i].Evidence...)
		}
		if count == 0 {
			it.State, it.Exact = "excluded_week", nil
		}
		for _, ev := range it.Evidence {
			r.Eligibility.DimensionRefs[it.Code[:1]] = append(r.Eligibility.DimensionRefs[it.Code[:1]], ev.SourceRef)
		}
	}
	for d, refs := range r.Eligibility.DimensionRefs {
		seen := map[string]bool{}
		unique := []string{}
		for _, ref := range refs {
			if !seen[ref] {
				seen[ref] = true
				unique = append(unique, ref)
			}
		}
		r.Eligibility.DimensionRefs[d] = unique
	}
	r.Eligibility.SourceDigest = observationDigest(r.Eligibility)
	r.ScoreReason = "按有效周汇总，未满足资格时保留观察"
	return r
}

func TestQualifiedWeekThreshold(t *testing.T) {
	for _, count := range []int{1, 19, 20, 100} {
		r := qualifiedWeek("2026-09-07", count)
		if e := r.Validate(); e != nil {
			t.Fatal(count, e)
		}
		if (r.Score != nil) != (count >= 20) || r.Eligibility.ActiveDays != 1 {
			t.Fatal("不得叠加日期门槛", count)
		}
	}
	r := qualifiedWeek("2026-09-07", 20)
	for i := range r.Eligibility.Observations {
		r.Eligibility.Observations[i].TaskKeys = []string{"one-task"}
	}
	r.Eligibility.SourceDigest = observationDigest(r.Eligibility)
	if e := r.Validate(); e != nil || r.Score != nil {
		t.Fatal("重复任务凑数", e)
	}
}

func TestQualifiedMonthCountAndNoZero(t *testing.T) {
	for count := 0; count <= 5; count++ {
		t.Run(fmt.Sprint(count), func(t *testing.T) {
			r := qualifiedMonth(count)
			if e := r.Validate(); e != nil {
				t.Fatal(e)
			}
			if r.Eligibility.ValidWeeks != count || (r.Rating != nil) != (count >= 3) || (r.Score != nil) != (count >= 3) {
				t.Fatal("有效周重算不符", r.Eligibility)
			}
			if count >= 3 && (*r.Score != "80.0" || *r.Rating != "L4") {
				t.Fatal("缺周被补零或另加任务门槛")
			}
			if e := r.Validate(); e != nil {
				t.Fatal("重验失败", e)
			}
		})
	}
}

func TestQualifiedMonthRejectsForgedWeeks(t *testing.T) {
	for name, change := range map[string]func(*Result){
		"异人": func(r *Result) { r.WeeklyReports[0].UserEmail = "other@example.test" },
		"旧版": func(r *Result) { r.WeeklyReports[0].PolicyVersions.Rules = "1.3" },
		"重复": func(r *Result) { r.WeeklyReports[1] = r.WeeklyReports[0] },
		"嵌套": func(r *Result) { r.WeeklyReports[0].WeeklyReports = []Result{sampleResult()} },
		"来源缺失": func(r *Result) {
			r.Eligibility.Observations = r.Eligibility.Observations[1:]
			r.Eligibility.SourceDigest = observationDigest(r.Eligibility)
		},
		"数值不同":  func(r *Result) { r.Items[0].Components[1].Exact = "90" },
		"排除有效周": func(r *Result) { r.Items[0].PeriodStates["2026-09-07"] = "excluded_week" },
		"采集未完":  func(r *Result) { r.WeeklyReports[0].Eligibility.SourcesComplete = false },
		"虚报任务":  func(r *Result) { r.WeeklyReports[0].Eligibility.Observations[0].OccurredAt = time.Time{} },
	} {
		t.Run(name, func(t *testing.T) {
			r := qualifiedMonth(3)
			change(&r)
			if r.Validate() == nil {
				t.Fatal("非法来源被接受")
			}
		})
	}
}

func TestQualifiedLowWeekStillCounts(t *testing.T) {
	r := qualifiedMonth(3)
	for j := range r.WeeklyReports {
		for i := range r.WeeklyReports[j].Items {
			r.WeeklyReports[j].Items[i].Exact = ptr("60")
		}
	}
	for i := range r.Items {
		r.Items[i].Exact = ptr("60")
		for j := range r.Items[i].Components {
			r.Items[i].Components[j].Exact = "60"
		}
	}
	if err := r.Validate(); err != nil {
		t.Fatal(err)
	}
	if r.Score == nil || *r.Score != "60.0" || r.Eligibility.ValidWeeks != 3 || *r.Rating != "L2" {
		t.Fatal("低分被错误剔除")
	}
}

func TestEvidenceOnlyWeekHasNoTaskCountGate(t *testing.T) {
	for _, count := range []int{1, 4, 5, 19, 20, 100} {
		r := qualifiedWeek("2026-09-07", count)
		r.PolicyVersions.Rules = RulesVersion
		r.PolicyVersions.Review = ReviewPolicyVersion
		r.Eligibility.Version = CurrentEligibilityVersion
		if err := r.Validate(); err != nil || r.Score == nil || *r.Score != "80.0" || !r.Eligibility.Eligible {
			t.Fatal("新规则不能按任务数拒绝已有四维依据", count, err)
		}
		if err := r.Validate(); err != nil {
			t.Fatal("新规则重验失败", err)
		}
	}
	r := qualifiedWeek("2026-09-07", 1)
	r.PolicyVersions.Rules, r.Eligibility.Version = RulesVersion, CurrentEligibilityVersion
	r.PolicyVersions.Review = ReviewPolicyVersion
	r.Eligibility.DimensionRefs["E"] = []string{}
	r.Eligibility.SourceDigest = observationDigest(r.Eligibility)
	if err := r.Validate(); err != nil || r.Score != nil {
		t.Fatal("取消数量门槛不能补造缺失的E依据", err)
	}
	r = qualifiedWeek("2026-09-07", 1)
	r.PolicyVersions.Rules, r.Eligibility.Version = RulesVersion, CurrentEligibilityVersion
	r.PolicyVersions.Review = ReviewPolicyVersion
	r.Eligibility.SourcesComplete = false
	if err := r.Validate(); err != nil || r.Score != nil || r.ScoreStatus != "data_pending" {
		t.Fatal("来源未完成仍应阻断综合分", err)
	}
}

func TestEvidenceOnlyMonthKeepsSameVersionWeeks(t *testing.T) {
	r := qualifiedMonth(5)
	r.PolicyVersions.Rules, r.Eligibility.Version = RulesVersion, CurrentEligibilityVersion
	r.PolicyVersions.Review = ReviewPolicyVersion
	for i := range r.WeeklyReports {
		r.WeeklyReports[i].PolicyVersions.Rules = RulesVersion
		r.WeeklyReports[i].PolicyVersions.Review = ReviewPolicyVersion
		r.WeeklyReports[i].Eligibility.Version = CurrentEligibilityVersion
		for j := range r.WeeklyReports[i].Eligibility.Observations {
			r.WeeklyReports[i].Eligibility.Observations[j].TaskKeys = []string{"one-goal"}
		}
		r.WeeklyReports[i].Eligibility.SourceDigest = observationDigest(r.WeeklyReports[i].Eligibility)
	}
	for i := range r.Eligibility.Observations {
		r.Eligibility.Observations[i].TaskKeys = []string{"one-goal"}
	}
	r.Eligibility.SourceDigest = observationDigest(r.Eligibility)
	if err := r.Validate(); err != nil || r.Score == nil || r.Eligibility.ValidWeeks != 5 || r.Eligibility.TaskCount != 1 {
		t.Fatal("同版月周来源不兼容", err)
	}
	r.WeeklyReports[0].PolicyVersions.Rules = "1.4"
	if r.Validate() == nil {
		t.Fatal("新月规则接受混版周评")
	}
}

func TestSemanticPolicyAndLegacy15Replay(t *testing.T) {
	r := qualifiedWeek("2026-09-07", 1)
	r.PolicyVersions.Rules, r.Eligibility.Version = "1.5", CurrentEligibilityVersion
	if err := r.Validate(); err != nil || r.Score == nil {
		t.Fatal("1.5旧报告不可因升版失效", err)
	}
	r.PolicyVersions.Rules = RulesVersion
	if r.Validate() == nil {
		t.Fatal("仅改版本不应成为新语义评审")
	}
	r.PolicyVersions.Review = ReviewPolicyVersion
	if err := r.Validate(); err != nil || r.Score == nil {
		t.Fatal("当前语义声明未接受", err)
	}
	r.PolicyVersions.Rules = "9.9"
	if r.Validate() == nil {
		t.Fatal("未知版本未拒绝")
	}
}
