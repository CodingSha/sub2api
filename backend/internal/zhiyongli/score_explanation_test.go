package zhiyongli

import (
	"strings"
	"testing"
)

func TestScoreExplanationDistinguishesOpportunityFromMissingEvidence(t *testing.T) {
	r := sampleResult()
	r.ScoreReason = "判断核验本期没有可评分依据"
	for i := range r.Items {
		if strings.HasPrefix(r.Items[i].Code, "J") {
			r.Items[i].State = "no_opportunity"
			r.Items[i].Exact = nil
		}
	}
	if err := r.Validate(); err != nil {
		t.Fatal(err)
	}
	r.Eligibility = &Eligibility{SourcesComplete: true}
	e := scoreExplanation(r)
	if e == nil || !strings.Contains(strings.Join(e.Reasons, ""), "判断核验") || !strings.Contains(strings.Join(e.Reasons, ""), "未观察到可评分") || !strings.Contains(e.Effect, "四个维度") {
		t.Fatal(e)
	}
	r.Items[6].State = "unobservable"
	e = scoreExplanation(r)
	if strings.Contains(strings.Join(e.Reasons, ""), "未观察到可评分") || !strings.Contains(strings.Join(e.Reasons, ""), "完整可评分依据") {
		t.Fatal(e)
	}
	r.Items[6].State = "no_opportunity"
	r.Eligibility.SourcesComplete = false
	r.ScoreStatus = "data_pending"
	e = scoreExplanation(r)
	if strings.Contains(strings.Join(e.Reasons, ""), "未观察到可评分") || !strings.Contains(strings.Join(e.Reasons, ""), "尚未完成") {
		t.Fatal(e)
	}
	r.ScoreStatus = "no_activity"
	r.Eligibility = nil
	r.ScoreReason = ""
	e = scoreExplanation(r)
	if strings.Contains(strings.Join(e.Reasons, ""), "维度") || !strings.Contains(e.Effect, "不按零分") {
		t.Fatal(e)
	}
}
