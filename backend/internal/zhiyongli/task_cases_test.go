package zhiyongli

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
	"testing"
	"time"
)

func TestTaskCaseEmptyPageKeepsRecordsArray(t *testing.T) {
	page := TaskCasePage{TaskCase: TaskCase{ID: strings.Repeat("c", 64)}, Records: []TaskRecord{}, Page: 2, PageSize: 50, Total: 1}
	raw, err := json.Marshal(page)
	if err != nil {
		t.Fatal(err)
	}
	var fields map[string]json.RawMessage
	if err = json.Unmarshal(raw, &fields); err != nil || string(fields["records"]) != "[]" {
		t.Fatal("末页之后仍须返回records空数组", string(raw), err)
	}
}

func taskSample() Result {
	r := sampleResult()
	at, _ := time.Parse(time.RFC3339, "2026-09-08T10:00:00+08:00")
	text := "先核对接口再改字段"
	hash := sha256.Sum256([]byte(text))
	c := TaskCase{ID: strings.Repeat("c", 64), TaskKey: "task:one", Title: "核对接口字段", Goal: "保持接口字段一致", Outcome: "有核查要求，未取得最终验收", ContextStatus: "available_records", RecordCount: 2, Limitations: []string{"工具记录未采集"}, Claims: []TaskClaim{{Text: "本人要求先核接口，再调整字段", ItemCodes: []string{"P3", "J2"}, RecordIDs: []string{"event:one"}}}}
	c.Records = []TaskRecord{{ID: "event:one", SourceHash: strings.Repeat("d", 64), TextHash: hex.EncodeToString(hash[:]), OccurredAt: &at, Role: "user", Origin: "direct_expression", Text: text}, {ID: "event:two", SourceHash: strings.Repeat("d", 64), TextHash: hex.EncodeToString(hash[:]), OccurredAt: &at, Role: "assistant", Origin: "assistant_output", Text: text}}
	r.TaskReview = &TaskReview{Version: TaskReviewVersion, Status: "case_checked", TaskKeys: []string{"task:one", "task:two"}, CheckedTaskKeys: []string{"task:one"}, ScopeNote: "仅核本案例，不能外推整周", Cases: []TaskCase{c}}
	return r
}
func TestTaskCaseRejectsFalseEvidence(t *testing.T) {
	r := taskSample()
	if e := r.Validate(); e != nil {
		t.Fatal(e)
	}
	cases := map[string]func(*Result){
		"跨任务引文":  func(r *Result) { r.TaskReview.Cases[0].Claims[0].RecordIDs = []string{"event:foreign"} },
		"伪造展示哈希": func(r *Result) { r.TaskReview.Cases[0].Records[0].Text = "擅改原文" },
		"伪造整周核查": func(r *Result) { r.TaskReview.Status = "week_checked" },
		"已核任务越界": func(r *Result) { r.TaskReview.CheckedTaskKeys = []string{"task:foreign"} },
		"隐去未知时间": func(r *Result) { r.TaskReview.Cases[0].Records[0].OccurredAt = nil },
		"AI归本人":  func(r *Result) { r.TaskReview.Cases[0].Records[1].Origin = "direct_expression" },
		"漏记录":    func(r *Result) { r.TaskReview.Cases[0].RecordCount = 3 },
		"跨截止时间": func(r *Result) {
			at, _ := time.Parse(time.RFC3339, "2026-09-14T00:00:00+08:00")
			r.TaskReview.Cases[0].Records[1].OccurredAt = &at
		},
		"含密钥": func(r *Result) { r.TaskReview.Cases[0].Claims[0].Text = "sk-" + strings.Repeat("a", 32) },
		"未知评价作用": func(r *Result) {
			r.TaskReview.Cases[0].Claims[0].Assessment = &ClaimAssessment{Kind: "plus10", Reason: "不允许额外加十分"}
		},
		"评价说明为空": func(r *Result) { r.TaskReview.Cases[0].Claims[0].Assessment = &ClaimAssessment{Kind: "support"} },
		"正向评价无子项": func(r *Result) {
			r.TaskReview.Cases[0].Claims[0].ItemCodes = nil
			r.TaskReview.Cases[0].Claims[0].Assessment = &ClaimAssessment{Kind: "support", Reason: "没有对应评分项"}
		},
	}
	for name, change := range cases {
		t.Run(name, func(t *testing.T) {
			r := taskSample()
			change(&r)
			if e := r.Validate(); e == nil {
				t.Fatal("错误任务依据被接受")
			}
		})
	}
}

func TestTaskClaimAssessmentDoesNotChangeScore(t *testing.T) {
	for _, kind := range []string{"support", "shortfall", "unresolved"} {
		r := taskSample()
		if err := r.Validate(); err != nil {
			t.Fatal(err)
		}
		before := *r.Score
		r.TaskReview.Cases[0].Claims[0].Assessment = &ClaimAssessment{Kind: kind, Reason: "只解释已有材料的评价作用，不改变分数"}
		if err := r.Validate(); err != nil {
			t.Fatal(err)
		}
		if before != *r.Score {
			t.Fatal("展示说明改变了评分")
		}
	}
}

func TestTaskReviewFormalVersionsOnly(t *testing.T) {
	for _, tc := range []struct{ version, status string }{
		{"sample-evidence-20261115.1", "initial"},
		{"source-evidence-20261116.1", "source_linked"},
		{TaskReviewVersion, "initial"}, {TaskReviewVersion, "source_linked"},
	} {
		r := taskSample()
		r.TaskReview.Version = tc.version
		r.TaskReview.Status = tc.status
		if r.Validate() == nil {
			t.Fatalf("非正式说明被接受: %s %s", tc.version, tc.status)
		}
	}
}
func TestTaskReviewStrictDecodeAndWeekCoverage(t *testing.T) {
	r := taskSample()
	raw, err := json.Marshal(r)
	if err != nil {
		t.Fatal(err)
	}
	var decoded Result
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	decoder.DisallowUnknownFields()
	if err = decoder.Decode(&decoded); err != nil {
		t.Fatal("正式字段被严格导入拒绝", err)
	}
	r.TaskReview.Status = "week_checked"
	r.TaskReview.CheckedTaskKeys = append([]string(nil), r.TaskReview.TaskKeys...)
	for _, item := range r.Items {
		r.TaskReview.WeekJudgments = append(r.TaskReview.WeekJudgments, WeekJudgment{
			ItemCode: item.Code, Reason: "综合已核任务说明该项判断", TaskKeys: []string{"task:one", "task:two"}, CaseIDs: []string{r.TaskReview.Cases[0].ID}, Limitations: []string{"代表案例不能替代全部任务"},
		})
	}
	if err = r.Validate(); err != nil {
		t.Fatal("完整十二项整周说明未通过", err)
	}
	for _, change := range []func(*Result){
		func(r *Result) { r.TaskReview.CheckedTaskKeys = []string{"task:one"} },
		func(r *Result) { r.TaskReview.WeekJudgments[0].TaskKeys = []string{"task:foreign"} },
		func(r *Result) { r.TaskReview.WeekJudgments[0].CaseIDs = []string{strings.Repeat("e", 64)} },
		func(r *Result) { r.TaskReview.WeekJudgments[0].ItemCode = r.TaskReview.WeekJudgments[1].ItemCode },
	} {
		raw, _ := json.Marshal(r)
		var bad Result
		if err := json.Unmarshal(raw, &bad); err != nil {
			t.Fatal(err)
		}
		change(&bad)
		if bad.Validate() == nil {
			t.Fatal("错误整周覆盖被接受")
		}
	}
}

func TestGeneratedTaskCaseDoesNotClaimSeparateReview(t *testing.T) {
	r := taskSample()
	r.TaskReview.Status = "generated"
	r.TaskReview.CheckedTaskKeys = []string{}
	if err := r.Validate(); err != nil {
		t.Fatal(err)
	}
	r.TaskReview.CheckedTaskKeys = []string{"task:one"}
	if err := r.Validate(); err == nil {
		t.Fatal("同次生成不能冒充另次核查")
	}
	r.TaskReview.CheckedTaskKeys = nil
	r.TaskReview.Cases[0].TaskKey = "task:foreign"
	if err := r.Validate(); err == nil {
		t.Fatal("同次生成也不能引用范围外任务")
	}
}
