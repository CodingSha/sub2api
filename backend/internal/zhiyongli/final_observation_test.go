package zhiyongli

import "testing"

// 月末恢复证据不足是本次明确无评级；只允许新资料合同且必须保留阻断。
func TestFinalObservationCannotTurnUnresolvedRecoveryIntoScore(t *testing.T) {
	days := []string{"2026-09-01", "2026-09-07", "2026-09-14", "2026-09-21"}
	r := withObservation(monthResult(), 20, days)
	r.Items[0].Components[0].Exact = "20"
	r.Items[0].Components[0].NecessaryGap = true
	r.Items[0].Exact = ptr("68")
	r.Assessment = reviewedScope()
	r.Assessment.Version = "scope-disclosure-20261118.3"
	r.Assessment.Issues[0].Status = "blocking"
	r.GapDispositions = []GapDisposition{{ItemCode: "P1", PeriodStart: "2026-09-01", Status: "unresolved", Reason: "记录没有决定性的后续恢复事实，本次不形成完整月评级"}}
	if err := r.Validate(); err != nil {
		t.Fatal(err)
	}
	if r.Score != nil || r.Exact != nil || r.Rating != nil || r.ScoreStatus != "observation_only" {
		t.Fatal("恢复证据不足被补成总分或评级")
	}
	r.Assessment.Version = ObservationAssessmentVersion
	if r.Validate() == nil {
		t.Fatal("旧资料合同不得隐式接受新处置")
	}
	r.Assessment.Version = "scope-disclosure-20261118.3"
	r.Assessment.Issues[0].Status = "limitation"
	if r.Validate() == nil {
		t.Fatal("不能解除阻断后仍把恢复证据不足当成确定状态")
	}
}
