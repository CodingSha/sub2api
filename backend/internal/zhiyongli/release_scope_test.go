package zhiyongli

import (
	"strings"
	"testing"
)

func reviewedScope() *Assessment {
	return &Assessment{Version: AssessmentVersion, SourceGapDigest: strings.Repeat("a", 64), Scope: "冻结可观察范围", Issues: []SourceIssue{{ID: strings.Repeat("b", 64), Status: "limitation", Description: "缺最终验收记录", Reason: "当前只评核验设计，未将其记为执行成功"}}}
}

func TestSourceLimitationsDoNotChangeScore(t *testing.T) {
	r := monthResult()
	r.Assessment = reviewedScope()
	if e := r.Validate(); e != nil {
		t.Fatal(e)
	}
	if *r.Score != "80.0" || *r.Rating != "L4" {
		t.Fatal("非阻断资料提示不应扣分或取消已核范围月级")
	}
	r.Assessment.Issues[0].Status = "blocking"
	if e := r.Validate(); e != nil {
		t.Fatal(e)
	}
	if *r.Score != "80.0" || r.Rating != nil || r.ScoreReason == "" {
		t.Fatal("阻断应保留可评分数并取消完整月级")
	}
}

func TestUnknownPeriodCannotBeWaivedByAssessment(t *testing.T) {
	r := monthResult()
	r.Assessment = reviewedScope()
	it := &r.Items[1]
	it.PeriodStates["2026-09-28"] = "unobservable"
	it.Components = it.Components[:4]
	it.Evidence = it.Evidence[:4]
	// 即使导入方误标所有资料非阻断，实际不可观察状态仍由后端取消月级。
	if e := r.Validate(); e != nil {
		t.Fatal(e)
	}
	if *r.Score != "80.0" || r.Rating != nil {
		t.Fatal("未知期间被补分或误评级")
	}
	r.Assessment = nil
	if e := r.Validate(); e == nil {
		t.Fatal("旧合同不能悄悄接受未知期间")
	}
}

func TestAssessmentVersionAndDuplicateIssues(t *testing.T) {
	r := monthResult()
	r.Assessment = reviewedScope()
	r.Assessment.Version = "unknown"
	if r.Validate() == nil {
		t.Fatal("未登记的资料处置版本")
	}
	r.Assessment.Version = AssessmentVersion
	r.Assessment.Issues = append(r.Assessment.Issues, r.Assessment.Issues[0])
	if r.Validate() == nil {
		t.Fatal("重复资料处置被接受")
	}
}
