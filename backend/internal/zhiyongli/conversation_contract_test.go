package zhiyongli

import "testing"

func TestNativeWorktreeChoiceUsesGitTerm(t *testing.T) {
	for _, name := range []string{"create_worktree", "mcp__codex_app__create_worktree"} {
		if !selectedWorkflowInTask(name, []string{"u"}, []DailyRecord{{ID: "u", Role: "user", Text: "在 worktree 单独处理本次改动。"}}) {
			t.Fatal("用户不必复述原生接口函数名", name)
		}
		if selectedWorkflowInTask(name, []string{"u"}, []DailyRecord{{ID: "u", Role: "user", Text: "查看 worktree_helper 文档。"}}) {
			t.Fatal("相似文件名不能当成选择", name)
		}
	}
}

func TestConversationDailyModelIsVersioned(t *testing.T) {
	for _, model := range []string{"gpt-6.1-sol", "gpt-6.2-sol"} {
		t.Run(model, func(t *testing.T) {
			r := dailyFixture()
			r.DailyReview.Model = model
			if r.Validate() == nil {
				t.Fatal("旧日评合同不能静默更换模型")
			}
			r.DailyReview.Version, r.PolicyVersions.Review = ConversationDailyVersion, ConversationDailyVersion
			if err := r.Validate(); err != nil {
				t.Fatal("新合同应记录实际采用的已登记模型", err)
			}
		})
	}
	r := dailyFixture()
	r.DailyReview.Version, r.PolicyVersions.Review = ConversationDailyVersion, ConversationDailyVersion
	r.DailyReview.Model = "unknown-model"
	if r.Validate() == nil {
		t.Fatal("未登记模型必须拒绝")
	}
}

func TestConversationObservationKeepsRawWithoutInventingScores(t *testing.T) {
	r := dailyFixture()
	r.DailyReview.Version, r.PolicyVersions.Review = ConversationDailyVersion, ConversationDailyVersion
	r.DailyReview.Behaviors = map[string]DailyBehavior{}
	r.ScoreStatus = "observation_only"
	for i := range r.Items {
		r.Items[i].State = "unobservable"
		r.Items[i].Exact, r.Items[i].Score = nil, nil
		r.Items[i].Evidence = nil
		r.Items[i].NecessaryGap = false
		r.Items[i].Reason = "本次材料未观察到可独立评价的本人行为。"
	}
	if err := r.Validate(); err != nil {
		t.Fatal("有原文但无可评分行为时应保留观察结论", err)
	}
	if r.Score != nil || len(r.DailyReview.Conversations) == 0 {
		t.Fatal("观察日不能补分或丢掉原文")
	}
	r.DailyReview.Version, r.PolicyVersions.Review = DailyReviewVersion, DailyReviewVersion
	if r.Validate() == nil {
		t.Fatal("新版观察合同不能改变旧版导入门禁")
	}
}

func TestConversationWeekWithOnlyObservationStillGetsZero(t *testing.T) {
	r := weeklyDailyFixture(t)
	r.WeeklyReview.Version, r.PolicyVersions.Review = conversationWeeklyVersion, conversationWeeklyVersion
	r.WeeklyReview.Behaviors = map[string]DailyBehavior{}
	for i := range r.Items {
		r.Items[i].State = "unobservable"
		r.Items[i].Exact, r.Items[i].Score = nil, nil
		r.Items[i].Evidence = nil
		r.Items[i].NecessaryGap = false
		r.Items[i].Reason = "五日抽样未观察到本项可独立评价的本人行为。"
	}
	if err := r.Validate(); err != nil {
		t.Fatal(err)
	}
	if r.Score == nil || *r.Score != "0.0" || len(r.WeeklyReview.Conversations) == 0 {
		t.Fatal("已核五日观察周应保留原文并显示0分", r.Score)
	}
}
