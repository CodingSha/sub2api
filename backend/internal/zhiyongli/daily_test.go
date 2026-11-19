package zhiyongli

import (
	"strings"
	"testing"
	"time"
)

func dailyFixture() Result {
	text := "请比较两份报价，说明差异原因。先计算再整理。逐项检查单价。沿用刚才提供的上下文。"
	sha := bytesSHA([]byte(text))
	d := &DailyReview{Version: DailyReviewVersion, Model: "deepseek-v4.1-flash-local", RequestDigest: strings.Repeat("a", 64), WindowStart: "2026-11-18T08:00:00+08:00", WindowEnd: "2026-11-18T21:00:00+08:00", SourceLabels: map[string]string{"u": "报价比较"}, SourceSessions: map[string]string{"u": "task-1"}, Conversations: map[string]DailyTask{"task-1": {Title: "报价比较", Goal: "比较报价差异", Records: []DailyRecord{{ID: "u", Role: "user", Text: text, SourceOrder: 0, SourceHash: sha}}}}}
	r := Result{UserEmail: "test@example.test", PeriodType: "day", PeriodStart: "2026-11-18", PeriodEnd: "2026-11-19", ScoreStatus: "scored", ScoreReason: "目标清楚，按安排推进。", SourceDigest: strings.Repeat("c", 64), DailyReview: d, PolicyVersions: Versions{Scale: ScaleVersion, Aggregation: DailyAggregationVersion, Review: DailyReviewVersion, Rules: RulesVersion, Rating: "G2-proposal28"}}
	r.WorkdayScope, _ = dailyWindow(r.PeriodStart)
	r.PolicyVersions.Scope = DailyReviewVersion
	d.Behaviors = map[string]DailyBehavior{}
	at, _ := time.Parse(time.RFC3339, "2026-11-18T09:30:00+08:00")
	for _, dimension := range []string{"P", "M", "J", "E"} {
		for i, tail := range []string{"1", "2", "3"} {
			code := dimension + tail
			item := Item{Code: code, State: "no_opportunity", Reason: "本次没有这一项独立行为。"}
			if i == 0 {
				n := "80"
				if dimension == "P" {
					n = "90"
				}
				item.State = "observed"
				item.Exact = &n
				item.Criterion = "原表现档标准"
				item.Reason = "明确了具体安排及其用途。"
				quote := map[string]string{"P": "请比较两份报价，说明差异原因。", "M": "先计算再整理。", "J": "逐项检查单价。", "E": "沿用刚才提供的上下文。"}[dimension]
				item.Evidence = []Evidence{{ID: code, SourceRef: "u", SourceHash: sha, TaskTitle: "报价比较", OccurredAt: at, Role: "user", Origin: "direct_expression", Quote: quote, Context: text, Supports: item.Reason}}
				d.Behaviors[code] = DailyBehavior{PrimaryItem: code, SourceRef: "u", Claim: itemNames[code] + "的具体安排"}
			}
			r.Items = append(r.Items, item)
		}
	}
	return r
}

func TestDailyExactScoresAndNoMonthlyRating(t *testing.T) {
	r := dailyFixture()
	if err := r.Validate(); err != nil {
		t.Fatal(err)
	}
	if r.Score == nil || *r.Score != "82.0" || r.Exact == nil || *r.Exact != "82" || r.Rating != nil {
		t.Fatalf("日分重算错误: %+v", r)
	}
	// 导入时不信任客户端总分；仅由原分项及权重重算。
	fake := "99.9"
	r.Score = &fake
	if err := r.Validate(); err != nil || *r.Score != "82.0" {
		t.Fatal("不应接受客户端伪造总分", err)
	}
}
func TestDailyRejectsBrokenEvidenceAndWrongWindow(t *testing.T) {
	tests := map[string]func(*Result){
		"旧日汇总版本": func(r *Result) { r.PolicyVersions.Aggregation = DailyReviewVersion },
		"原文被改": func(r *Result) {
			task := r.DailyReview.Conversations["task-1"]
			task.Records[0].Text = "另一段话"
			r.DailyReview.Conversations["task-1"] = task
		},
		"旧日引文": func(r *Result) {
			r.Items[0].Evidence[0].OccurredAt = r.Items[0].Evidence[0].OccurredAt.AddDate(0, 0, -1)
		},
		"窗口外引文": func(r *Result) {
			r.Items[0].Evidence[0].OccurredAt, _ = time.Parse(time.RFC3339, "2026-11-18T21:00:00+08:00")
		},
		"工具冒认本人":  func(r *Result) { r.Items[0].Evidence[0].Role = "tool" },
		"未登记模型":   func(r *Result) { r.DailyReview.Model = "other" },
		"缺请求记录":   func(r *Result) { r.DailyReview.RequestDigest = "" },
		"伪装周范围":   func(r *Result) { r.PeriodEnd = "2026-11-25" },
		"未决状态":    func(r *Result) { r.Items[0].State = "pending"; r.Items[0].Exact = nil },
		"伪造来源关系":  func(r *Result) { r.DailyReview.SourceSessions["u"] = "task-2" },
		"同行为重复计分": func(r *Result) { r.Items[3].Evidence[0] = r.Items[0].Evidence[0] },
		"无行为归属":   func(r *Result) { r.DailyReview.Behaviors = nil },
		"改名重复主张":  func(r *Result) { r.DailyReview.Behaviors["other"] = r.DailyReview.Behaviors["P1"] },
	}
	for name, change := range tests {
		t.Run(name, func(t *testing.T) {
			r := dailyFixture()
			change(&r)
			if r.Validate() == nil {
				t.Fatal("应拒绝无效日评")
			}
		})
	}
}

func TestDailyDifferentClaimsNeedDistinctQuotesWithinFullContext(t *testing.T) {
	r := dailyFixture()
	// 完整消息保持一致，独立行为各自引用原文片段。
	if err := r.Validate(); err != nil {
		t.Fatal("不同主张应允许共用完整上下文", err)
	}
	whole := r.Items[0].Evidence[0].Context
	for _, index := range []int{0, 6} {
		ev := &r.Items[index].Evidence[0]
		ev.Quote = whole
	}
	if r.Validate() == nil {
		t.Fatal("不能靠换主张重复认领同段原话")
	}
	// 相同主归ID跨项使用仍须拒绝，不能仅换一个引文片段绕过。
	r = dailyFixture()
	r.Items[6].Evidence[0].ID = r.Items[0].Evidence[0].ID
	if r.Validate() == nil {
		t.Fatal("同一独立行为不能跨项认领")
	}
}
func TestDailyMissingDimensionDoesNotInventScore(t *testing.T) {
	r := dailyFixture()
	for i := range r.Items {
		if strings.HasPrefix(r.Items[i].Code, "E") {
			r.Items[i].State = "no_opportunity"
			r.Items[i].Exact = nil
			r.Items[i].Evidence = nil
		}
	}
	if err := r.Validate(); err != nil {
		t.Fatal(err)
	}
	if r.Score == nil || *r.Score != "82.4" || r.Exact == nil || *r.Exact != "1400/17" || r.ScoreStatus != "scored" || r.Dimensions[3].Score != nil {
		t.Fatal("日总分须归一已观察维度权重，缺失维度不能补分")
	}
	for i := range r.Items {
		r.Items[i].State = "no_opportunity"
		r.Items[i].Exact = nil
		r.Items[i].Evidence = nil
	}
	if r.Validate() == nil {
		t.Fatal("十二项全空应视为执行失败")
	}
}

func TestDailyToolDirectoryDoesNotRequireUnreadBodyOrImplyPersonalSelection(t *testing.T) {
	r := dailyFixture()
	task := r.DailyReview.Conversations["task-1"]
	task.ToolIndex = []DailyToolIndex{{Name: "push", RecordID: "tool-1", Chars: 5000}}
	task.Workflow = []DailyWorkflow{{Name: "push", Purpose: "调用既有提交流程", Selection: "AI自主调用", UserRefs: []string{}, ToolRefs: []string{"tool-1"}}}
	r.DailyReview.Conversations["task-1"] = task
	if err := r.Validate(); err != nil {
		t.Fatal("目录足以呈现已调用，不能强制补读工具全文", err)
	}
	task.Workflow[0].Selection = "用户明确选择"
	r.DailyReview.Conversations["task-1"] = task
	if r.Validate() == nil {
		t.Fatal("AI调用不能冒充用户选择")
	}
	task.Workflow[0].Selection = "AI自主调用"
	task.Workflow[0].ToolRefs = []string{"another-task-tool"}
	r.DailyReview.Conversations["task-1"] = task
	if r.Validate() == nil {
		t.Fatal("工具目录必须属于同任务")
	}
}

func TestDailyTrivialQueriesRemainUnscored(t *testing.T) {
	r := dailyFixture()
	r.ScoreStatus = "observation_only"
	r.ScoreReason = "当日只有简短查询，没有形成可评分的完整任务。"
	r.Items = nil
	r.DailyReview.Conversations = map[string]DailyTask{}
	r.DailyReview.Behaviors = map[string]DailyBehavior{}
	r.DailyReview.SourceSessions = map[string]string{}
	r.DailyReview.SourceLabels = map[string]string{}
	if err := r.Validate(); err != nil || r.Score != nil || r.Exact != nil {
		t.Fatal("简短查询不能造分或成为待审", err)
	}
}
