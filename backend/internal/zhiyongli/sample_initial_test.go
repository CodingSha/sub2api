package zhiyongli

import (
	"fmt"
	"strings"
	"testing"
)

func TestSampleInitialReferencePreservesMissingDimension(t *testing.T) {
	r := sampleResult()
	r.SampleInitial = &SampleInitial{Version: SampleInitialVersion, SampleDigest: strings.Repeat("b", 64), AuditStatus: "not_reviewed", Score: ptr("100")}
	for i := range r.Items {
		if strings.HasPrefix(r.Items[i].Code, "E") {
			r.Items[i].State, r.Items[i].Exact = "no_opportunity", nil
		}
	}
	r.ScoreReason = "使用效率尚无可评依据"
	if err := r.Validate(); err != nil {
		t.Fatal(err)
	}
	if r.Score != nil || r.Dimensions[3].Score != nil || *r.SampleInitial.Score != "80.0" || r.SampleInitial.CoveredWeight != 85 || strings.Join(r.SampleInitial.MissingDimensions, ",") != "E" {
		t.Fatalf("参考分不得补齐正式总分或缺失维度：%+v", r.SampleInitial)
	}
	r.SampleInitial.AuditStatus = "passed"
	if r.Validate() == nil {
		t.Fatal("初评不得伪装复查通过")
	}
}

func TestSourceLinkedPreservesScoreWithoutClaimingReview(t *testing.T) {
	r := initialTaskSample()
	r.SampleInitial = nil
	r.TaskReview.Version, r.TaskReview.Status = SourceReviewVersion, "source_linked"
	if err := r.Validate(); err != nil {
		t.Fatal(err)
	}
	if r.SampleInitial != nil || len(r.TaskReview.CheckedTaskKeys) != 0 || len(r.TaskReview.WeekJudgments) != 0 {
		t.Fatal("关联来源不能升级初评或复查状态")
	}
	score := *r.Score
	r.TaskReview.Cases[0].Claims[0].Text = "不属于原评分的新增判断"
	if r.Validate() == nil || *r.Score != score {
		t.Fatal("不得引入新判断或改变已有分数")
	}
}

func TestInitialNarrativeRetainsLargeCompleteGroup(t *testing.T) {
	r := initialTaskSample()
	c := &r.TaskReview.Cases[0]
	for i := len(c.Records); i < 1140; i++ {
		e := c.Records[1]
		e.ID = fmt.Sprintf("event:extra%d", i)
		c.Records = append(c.Records, e)
	}
	c.RecordCount = len(c.Records)
	if err := r.Validate(); err != nil {
		t.Fatal(err)
	}
	r.TaskReview.Version, r.TaskReview.Status = TaskReviewVersion, "case_checked"
	r.TaskReview.CheckedTaskKeys = r.TaskReview.TaskKeys
	if r.Validate() == nil {
		t.Fatal("不得扩大原正式任务案例容量")
	}
}

func initialTaskSample() Result {
	r := taskSample()
	r.SampleInitial = &SampleInitial{Version: SampleInitialVersion, SampleDigest: strings.Repeat("b", 64), AuditStatus: "not_reviewed"}
	r.TaskReview.Version, r.TaskReview.Status = SampleReviewVersion, "initial"
	r.TaskReview.CheckedTaskKeys = []string{}
	c := &r.TaskReview.Cases[0]
	c.Records[0].ID = r.Items[0].Evidence[0].SourceRef
	c.Records[0].SourceHash = r.Items[0].Evidence[0].SourceHash
	c.Records[0].OccurredAt = &r.Items[0].Evidence[0].OccurredAt
	c.Records[1].OccurredAt = c.Records[0].OccurredAt
	c.Claims = []TaskClaim{{Text: r.Items[0].Reason, ItemCodes: []string{"P1"}, RecordIDs: []string{c.Records[0].ID}}}
	return r
}

func TestInitialNarrativeRequiresOriginalReasonAndEvidence(t *testing.T) {
	r := initialTaskSample()
	if err := r.Validate(); err != nil {
		t.Fatal(err)
	}
	for name, change := range map[string]func(*Result){
		"伪造已核范围":  func(r *Result) { r.TaskReview.CheckedTaskKeys = []string{"task:one"} },
		"替换原评分理由": func(r *Result) { r.TaskReview.Cases[0].Claims[0].Text = "新增未经原评分支持的结论" },
		"不相关原文":   func(r *Result) { r.TaskReview.Cases[0].Claims[0].RecordIDs = []string{"event:two"} },
		"夹带复查结论": func(r *Result) {
			r.TaskReview.Cases[0].Claims[0].Assessment = &ClaimAssessment{Kind: "support", Reason: "复查确认"}
		},
	} {
		t.Run(name, func(t *testing.T) {
			r := initialTaskSample()
			change(&r)
			if r.Validate() == nil {
				t.Fatal("接受了伪造的初评说明")
			}
		})
	}
}
