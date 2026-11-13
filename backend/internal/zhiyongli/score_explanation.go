package zhiyongli

import "strings"

// ScoreExplanation是读取时生成的展示字段，不改冻结结果、资格计算或已发布修订。
type ScoreExplanation struct {
	Reasons []string `json:"reasons"`
	Effect  string   `json:"effect"`
}

func scoreExplanation(r Result) *ScoreExplanation {
	if r.Score != nil {
		return nil
	}
	value := &ScoreExplanation{Reasons: []string{}}
	add := func(text string) {
		text = strings.TrimSpace(text)
		if text == "" {
			return
		}
		for _, existing := range value.Reasons {
			if existing == text {
				return
			}
		}
		value.Reasons = append(value.Reasons, text)
	}
	missing, absent := []string{}, []string{}
	for _, code := range []string{"P", "M", "J", "E"} {
		for _, d := range r.Dimensions {
			if d.Code != code || d.Score != nil {
				continue
			}
			missing = append(missing, d.Name)
			// 只有三项均明确为无机会，才表述为未观察到可评分行为。
			count := 0
			for _, it := range r.Items {
				if strings.HasPrefix(it.Code, code) && it.State == "no_opportunity" {
					count++
				}
			}
			if count == 3 {
				absent = append(absent, d.Name)
			}
		}
	}
	if len(missing) > 0 && r.ScoreStatus != "no_activity" {
		if len(absent) == len(missing) && r.ScoreStatus != "data_pending" && r.Eligibility != nil && r.Eligibility.SourcesComplete {
			add("本期记录中未观察到可评分的“" + strings.Join(missing, "、") + "”行为，这些维度没有评分。")
		} else {
			add("本期记录未提供“" + strings.Join(missing, "、") + "”维度的完整可评分依据，这些维度没有评分。")
		}
	}
	if r.Eligibility != nil {
		for _, reason := range r.Eligibility.Reasons {
			if reason == "来源或期间评测尚未完成" {
				reason = "本期资料收集或评审尚未完成。"
			}
			duplicate := false
			for _, code := range []string{"P", "M", "J", "E"} {
				if reason == code+"维度尚无可评本人依据" || reason == code+"维度没有可评数值" {
					// 缺分原因已由维度统一说明；有数值但缺本人依据时仍须保留。
					for _, d := range r.Dimensions {
						if d.Code == code && d.Score == nil {
							duplicate = true
						}
					}
				}
				reason = strings.ReplaceAll(reason, code+"维度", dimensionNames[code])
			}
			if !duplicate {
				add(reason)
			}
		}
	}
	switch r.ScoreStatus {
	case "no_activity":
		add("本期没有可用于评审的使用记录。")
	case "data_pending":
		add("本期资料收集或评审尚未完成。")
	}
	// 历史报告缺少结构化资格时保留原说明，不能猜测其他阻塞原因。
	if r.Eligibility == nil {
		add(r.ScoreReason)
	}
	if len(value.Reasons) == 0 {
		add(r.ScoreReason)
	}
	period := "周"
	if r.PeriodType == "month" {
		period = "月"
	}
	if len(missing) > 0 && r.ScoreStatus != "no_activity" {
		value.Effect = period + "总分需要四个维度都有评分，因此本期未生成总分。已有的分项和维度评分仍保留。"
	} else if r.ScoreStatus == "no_activity" {
		value.Effect = "没有使用记录不按零分处理，本期不生成评分。"
	} else {
		value.Effect = "本期尚未满足生成" + period + "总分的条件。已有的分项和维度评分仍保留。"
	}
	return value
}
