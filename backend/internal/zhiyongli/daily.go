package zhiyongli

import (
	"encoding/json"
	"math/big"
	"strings"
	"time"
)

const DailyReviewVersion = "daily-task-review-20261120.2"
const DailyAggregationVersion = "daily-observed-weighted-20261119.2"

// DailyReview 保存实际交给模型的任务上下文；列表接口不返回正文。
type DailyReview struct {
	Version        string                   `json:"version"`
	Model          string                   `json:"model"`
	RequestDigest  string                   `json:"request_digest"`
	WindowStart    string                   `json:"window_start"`
	WindowEnd      string                   `json:"window_end"`
	SourceLabels   map[string]string        `json:"source_labels"`
	SourceSessions map[string]string        `json:"source_sessions"`
	Conversations  map[string]DailyTask     `json:"conversations"`
	Behaviors      map[string]DailyBehavior `json:"behaviors"`
}

// DailyBehavior 绑定独立行为及其唯一主归；不同主张可以共用一段原话。
type DailyBehavior struct {
	PrimaryItem string `json:"primary_item"`
	SourceRef   string `json:"source_ref"`
	Claim       string `json:"claim"`
}
type DailyTask struct {
	Title     string           `json:"title"`
	Goal      string           `json:"goal"`
	Outcome   string           `json:"outcome"`
	Workflow  []DailyWorkflow  `json:"workflow"`
	ToolIndex []DailyToolIndex `json:"tool_index,omitempty"`
	Records   []DailyRecord    `json:"records"`
}

// DailyToolIndex 只是模型收到的调用目录，不冒充已阅读的工具正文或执行成功。
type DailyToolIndex struct {
	Name     string `json:"name"`
	RecordID string `json:"record_id"`
	Chars    int    `json:"chars"`
}
type DailyWorkflow struct {
	Name      string   `json:"name"`
	Purpose   string   `json:"purpose"`
	Selection string   `json:"selection"`
	UserRefs  []string `json:"user_refs"`
	ToolRefs  []string `json:"tool_refs"`
}
type DailyRecord struct {
	ID          string   `json:"id"`
	Role        string   `json:"role"`
	Text        string   `json:"text"`
	Excerpt     bool     `json:"excerpt"`
	SourceOrder int      `json:"source_order"`
	SourceHash  string   `json:"source_hash"`
	Names       []string `json:"names"`
}

func (r *Result) validateDaily() error {
	d := r.DailyReview
	if !strings.Contains(r.UserEmail, "@") || !hashValid(r.SourceDigest) || d == nil || d.Version != DailyReviewVersion || d.Model != "deepseek-v4.1-flash-local" || !hashValid(d.RequestDigest) {
		return invalid("日评缺少账户、冻结来源、模型或请求记录")
	}
	if r.PolicyVersions.Scale != ScaleVersion || r.PolicyVersions.Rules != RulesVersion || r.PolicyVersions.Aggregation != DailyAggregationVersion || r.PolicyVersions.Review != DailyReviewVersion || r.PolicyVersions.Rating != "G2-proposal28" {
		return invalid("日评分版本未登记")
	}
	a, err := date(r.PeriodStart)
	if err != nil {
		return err
	}
	b, err := date(r.PeriodEnd)
	if err != nil {
		return err
	}
	if !b.Equal(a.AddDate(0, 0, 1)) || d.WindowStart != r.PeriodStart+"T08:00:00+08:00" || d.WindowEnd != r.PeriodStart+"T18:00:00+08:00" {
		return invalid("日评分须对应北京时间当日08至18点，结束日期为次日")
	}
	if r.WorkdayScope == nil || r.PolicyVersions.Scope != DailyReviewVersion {
		return invalid("日评分缺少固定采集窗口合同")
	}
	if err := r.WorkdayScope.validate(r.PeriodStart, r.PeriodEnd); err != nil {
		return err
	}
	closed, _ := time.Parse(time.RFC3339, r.PeriodStart+"T23:00:00+08:00")
	if closed.After(time.Now()) {
		return invalid("日评分尚未到23点执行时间")
	}
	if r.TaskReview != nil || r.SampleInitial != nil || r.Eligibility != nil || r.Assessment != nil || len(r.Coverage) > 0 || len(r.WeeklyReports) > 0 || len(r.GapDispositions) > 0 {
		return invalid("日评分不能混用周月观察资格、抽样初评或汇总来源")
	}
	raw, _ := json.Marshal(d)
	if len(raw) > 160000 || len(d.Conversations) > 5 || credentialPattern.Match(raw) {
		return invalid("日评上下文超限或含未脱敏凭证")
	}
	records := map[string]DailyRecord{}
	taskOf := map[string]string{}
	for key, task := range d.Conversations {
		if !sourceRefPattern.MatchString(key) || strings.TrimSpace(task.Title) == "" || strings.TrimSpace(task.Goal) == "" || len(task.Records) == 0 {
			return invalid("日评任务缺少目标或实际对话")
		}
		tools := map[string]map[string]bool{}
		for _, entry := range task.ToolIndex {
			if !sourceRefPattern.MatchString(entry.RecordID) || strings.TrimSpace(entry.Name) == "" || entry.Chars <= 0 {
				return invalid("日评工具目录记录无效")
			}
			if tools[entry.RecordID] == nil {
				tools[entry.RecordID] = map[string]bool{}
			}
			tools[entry.RecordID][entry.Name] = true
		}
		for order, record := range task.Records {
			if !sourceRefPattern.MatchString(record.ID) || record.Text == "" || record.SourceOrder != order || record.SourceHash != bytesSHA([]byte(record.Text)) || record.Role != "user" && record.Role != "assistant" && record.Role != "tool" && record.Role != "context" {
				return invalid("日评对话角色、顺序或正文签名无效")
			}
			if previous, exists := records[record.ID]; exists {
				if (record.Role != "assistant" && record.Role != "context") || previous.Role != record.Role || previous.Text != record.Text {
					return invalid("日评任务存在重复记录或冲突正文")
				}
			}
			records[record.ID] = record
			taskOf[record.ID] = key
		}
		for _, usage := range task.Workflow {
			if strings.TrimSpace(usage.Name) == "" || strings.TrimSpace(usage.Purpose) == "" || len(usage.ToolRefs) == 0 {
				return invalid("工作流缺少用途或实际调用")
			}
			if usage.Selection != "用户明确选择" && usage.Selection != "AI自主调用" || usage.Selection == "AI自主调用" && len(usage.UserRefs) > 0 || usage.Selection == "用户明确选择" && len(usage.UserRefs) == 0 {
				return invalid("工作流本人归属无效")
			}
			for _, ref := range usage.UserRefs {
				rec, ok := records[ref]
				if !ok || taskOf[ref] != key || rec.Role != "user" || !strings.Contains(rec.Text, usage.Name) {
					return invalid("本人工作流选择没有同任务原话")
				}
			}
			for _, ref := range usage.ToolRefs {
				rec, ok := records[ref]
				if !tools[ref][usage.Name] && (!ok || taskOf[ref] != key || rec.Role != "tool") {
					return invalid("工作流没有同任务实际工具记录")
				}
			}
		}
	}
	for ref, key := range d.SourceSessions {
		if taskOf[ref] != key || d.SourceLabels[ref] != d.Conversations[key].Title {
			return invalid("日评引文与任务来源不一致")
		}
	}
	if len(d.SourceLabels) != len(d.SourceSessions) {
		return invalid("日评来源标签不完整")
	}
	r.Exact, r.Score, r.Rating = nil, nil, nil
	r.Dimensions = []Dimension{}
	if r.ScoreStatus == "no_activity" {
		if len(r.Items) > 0 || len(d.Conversations) > 0 || len(d.Behaviors) > 0 || strings.TrimSpace(r.ScoreReason) == "" {
			return invalid("无使用记录的日评不能携带任务或分数")
		}
		return nil
	}
	if r.ScoreStatus == "observation_only" && len(d.Conversations) == 0 && len(d.Behaviors) == 0 && len(r.Items) == 0 && strings.TrimSpace(r.ScoreReason) != "" {
		return nil
	}
	if r.ScoreStatus != "scored" && r.ScoreStatus != "observation_only" || len(r.Items) != 12 || len(d.Conversations) == 0 {
		return invalid("日评须为完整十二项终态，执行失败不能作为人员报告")
	}
	seen := map[string]bool{}
	if len(d.Behaviors) == 0 || len(d.Behaviors) > 40 {
		return invalid("日评缺少独立行为归属或行为数量超限")
	}
	claims := map[string]bool{}
	for id, behavior := range d.Behaviors {
		record, ok := records[behavior.SourceRef]
		_, itemOK := itemNames[behavior.PrimaryItem]
		identity := behavior.SourceRef + "\x00" + strings.TrimSpace(behavior.Claim)
		if !sourceRefPattern.MatchString(id) || !itemOK || !ok || record.Role != "user" || strings.TrimSpace(behavior.Claim) == "" || len(behavior.Claim) > 1200 || claims[identity] || credentialPattern.MatchString(behavior.Claim) {
			return invalid("日评独立行为须有本人原文和唯一主归，不能换项重复认领")
		}
		claims[identity] = true
	}
	values := map[string][]*big.Rat{}
	gaps := map[string][]*big.Rat{}
	start, _ := time.Parse(time.RFC3339, d.WindowStart)
	end, _ := time.Parse(time.RFC3339, d.WindowEnd)
	type quoteSpan struct {
		ref, code  string
		start, end int
	}
	spans := []quoteSpan{}
	for i := range r.Items {
		item := &r.Items[i]
		name, ok := itemNames[item.Code]
		if !ok || seen[item.Code] || strings.TrimSpace(item.Reason) == "" || credentialPattern.MatchString(item.Reason) || credentialPattern.MatchString(item.Criterion) || len(item.Components) > 0 || len(item.PeriodStates) > 0 {
			return invalid("日评分项重复、缺少理由或混有汇总状态")
		}
		seen[item.Code] = true
		item.Name = name
		item.Score = nil
		if item.State != "observed" {
			if item.State != "no_opportunity" && item.State != "unobservable" && item.State != "related" && item.State != "not_applicable" || item.Exact != nil || item.NecessaryGap || len(item.Evidence) > 0 {
				return invalid("未计分项不能补分、挂计分证据或进入待审核状态")
			}
			continue
		}
		if item.Exact == nil || strings.TrimSpace(item.Criterion) == "" || len(item.Evidence) == 0 {
			return invalid("日评分项缺少档位、标准或本人依据")
		}
		n, err := rational(*item.Exact)
		if err != nil {
			return err
		}
		allowed := false
		for _, score := range []int64{0, 20, 40, 60, 70, 80, 90, 100} {
			if n.Cmp(big.NewRat(score, 1)) == 0 {
				allowed = true
			}
		}
		if !allowed || item.NecessaryGap != (n.Cmp(big.NewRat(40, 1)) <= 0) {
			return invalid("日分项须沿用原表现档及必要缺口规则")
		}
		evidenceIDs := map[string]bool{}
		for _, ev := range item.Evidence {
			record, ok := records[ev.SourceRef]
			if !ok || ev.ID == "" || evidenceIDs[ev.ID] || ev.Role != "user" || record.Role != "user" || ev.Origin != "direct_expression" && ev.Origin != "explicit_adoption" && !(item.Code[0] == 'P' && ev.Origin == "quoted_or_forwarded") || ev.SourceHash != record.SourceHash || ev.Context != record.Text || ev.Quote == "" || !strings.Contains(record.Text, ev.Quote) || ev.Supports == "" || ev.TaskTitle != d.Conversations[taskOf[ev.SourceRef]].Title || d.SourceSessions[ev.SourceRef] != taskOf[ev.SourceRef] || ev.OccurredAt.Before(start) || !ev.OccurredAt.Before(end) || credentialPattern.MatchString(ev.Supports) {
				return invalid("日评分项依据须绑定当日窗口内的本人原话与实际交付上下文")
			}
			behavior, exists := d.Behaviors[ev.ID]
			if !exists || behavior.PrimaryItem != item.Code || behavior.SourceRef != ev.SourceRef {
				return invalid("计分依据须绑定该分项独立行为，不能跨项重复计分")
			}
			if strings.Count(record.Text, ev.Quote) != 1 {
				return invalid("计分引文不能唯一定位到本人原文")
			}
			lo := strings.Index(record.Text, ev.Quote)
			hi := lo + len(ev.Quote)
			for _, span := range spans {
				if span.ref == ev.SourceRef && span.code != item.Code && lo < span.end && span.start < hi {
					return invalid("同段本人原话不能换主张跨项重复计分；独立行为须绑定各自片段")
				}
			}
			spans = append(spans, quoteSpan{ev.SourceRef, item.Code, lo, hi})
			evidenceIDs[ev.ID] = true
		}
		item.Exact = exact(n)
		item.Score = display(n)
		key := item.Code[:1]
		values[key] = append(values[key], n)
		if item.NecessaryGap {
			gaps[key] = append(gaps[key], n)
		}
	}
	total := new(big.Rat)
	var observedWeight int64
	observed := 0
	for _, code := range []string{"P", "M", "J", "E"} {
		var value *big.Rat
		if len(gaps[code]) > 0 {
			value = new(big.Rat).Set(gaps[code][0])
			for _, n := range gaps[code][1:] {
				if n.Cmp(value) < 0 {
					value.Set(n)
				}
			}
		} else if len(values[code]) > 0 {
			value = new(big.Rat)
			for _, n := range values[code] {
				value.Add(value, n)
			}
			value.Quo(value, big.NewRat(int64(len(values[code])), 1))
		}
		observed += len(values[code])
		r.Dimensions = append(r.Dimensions, Dimension{code, dimensionNames[code], exact(value), display(value)})
		if value != nil {
			observedWeight += weights[code]
			total.Add(total, new(big.Rat).Mul(value, big.NewRat(weights[code], 1)))
		}
	}
	if observed == 0 {
		return invalid("已选任务却十二项全空，属于评审执行失败")
	}
	total.Quo(total, big.NewRat(observedWeight, 1))
	r.Exact, r.Score = exact(total), display(total)
	r.ScoreStatus = "scored"
	return nil
}

// dailyWindow 是每日固定采集、执行及交付时间，不继承周报告的封口时刻。
func dailyWindow(start string) (*WorkdayScope, error) {
	tz := time.FixedZone("Asia/Shanghai", 8*3600)
	day, err := time.ParseInLocation("2006-01-02", start, tz)
	if err != nil || day.Format("2006-01-02") != start {
		return nil, invalid("日评分日期无效")
	}
	due := day.AddDate(0, 0, 1).Add(8 * time.Hour).Format(time.RFC3339)
	return &WorkdayScope{Version: DailyReviewVersion, RegistrySHA: bytesSHA([]byte(DailyReviewVersion + ":08:00-18:00:23:00:08:00")), PeriodStart: start, PeriodEnd: day.AddDate(0, 0, 1).Format("2006-01-02"), WorkingDates: []string{start}, ObservedUntil: day.Add(18 * time.Hour).Format(time.RFC3339), FreezeAt: day.Add(23 * time.Hour).Format(time.RFC3339), CandidateDueAt: &due}, nil
}
