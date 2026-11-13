// Package zhiyongli 保存固定版本评测结果并控制发布，不在接口中调用模型或手工改分。
package zhiyongli

import (
	"errors"
	"fmt"
	"math/big"
	"regexp"
	"strings"
	"time"
)

const ScaleVersion = "pmje-performance-20261105.023-proposal1.control28"
const MonthVersion = "pmje-month-rollup-20261105.024"
const RulesVersion = "1.6"
const ReviewPolicyVersion = "contextual-review-20261109.1"
const QualifiedMonthVersion = "pmje-month-rollup-20261106.025"
const AssessmentVersion = "scope-disclosure-20261106.1"

var ErrNotFound = errors.New("报告不存在")
var ErrConflict = errors.New("候选版本已过期，请刷新后检查最新结果")

type InvalidError struct{ Message string }

func (e *InvalidError) Error() string { return e.Message }
func invalid(s string) error          { return &InvalidError{s} }

type Evidence struct {
	ID          string    `json:"id"`
	SourceRef   string    `json:"source_ref"`
	SourceHash  string    `json:"source_hash"`
	TaskTitle   string    `json:"task_title"`
	OccurredAt  time.Time `json:"occurred_at"`
	Role        string    `json:"role"`
	Origin      string    `json:"origin"`
	Quote       string    `json:"quote"`
	Supports    string    `json:"supports"`
	Context     string    `json:"context"`
	Limitations []string  `json:"limitations"`
}
type Component struct {
	PeriodStart  string `json:"period_start"`
	PeriodEnd    string `json:"period_end"`
	Exact        string `json:"exact"`
	NecessaryGap bool   `json:"necessary_gap"`
}
type Coverage struct {
	PeriodStart     string `json:"period_start"`
	PeriodEnd       string `json:"period_end"`
	EvidenceChecked bool   `json:"evidence_checked"`
}
type GapDisposition struct {
	ItemCode            string   `json:"item_code"`
	PeriodStart         string   `json:"period_start"`
	Status              string   `json:"status"`
	Reason              string   `json:"reason"`
	RecoveryPeriod      string   `json:"recovery_period"`
	RecoveryEvidenceIDs []string `json:"recovery_evidence_ids"`
}
type Item struct {
	Code         string            `json:"item_code"`
	Name         string            `json:"name"`
	State        string            `json:"state"`
	Exact        *string           `json:"exact"`
	Score        *string           `json:"score"`
	Reason       string            `json:"reason"`
	Criterion    string            `json:"criterion"`
	NecessaryGap bool              `json:"necessary_gap"`
	Components   []Component       `json:"components"`
	PeriodStates map[string]string `json:"period_states"`
	Evidence     []Evidence        `json:"evidence"`
	Limitations  []string          `json:"limitations"`
}
type Dimension struct {
	Code  string  `json:"code"`
	Name  string  `json:"name"`
	Exact *string `json:"exact"`
	Score *string `json:"score"`
}
type Versions struct {
	Scale       string `json:"scale"`
	Aggregation string `json:"aggregation"`
	Rating      string `json:"rating"`
	Rules       string `json:"rules"`
	Review      string `json:"review,omitempty"`
	Scope       string `json:"scope,omitempty"`
}

// 资料提示与已证的本人必要缺口不同；新观察合同只豁免已有充分其他期间依据的局部不可观察。
type SourceIssue struct {
	ID          string `json:"id"`
	Status      string `json:"status"`
	Description string `json:"description"`
	Reason      string `json:"reason"`
}
type Assessment struct {
	Version         string        `json:"version"`
	SourceGapDigest string        `json:"source_gap_digest"`
	Scope           string        `json:"scope"`
	Issues          []SourceIssue `json:"issues"`
}
type Result struct {
	UserEmail       string           `json:"user_email"`
	PeriodType      string           `json:"period_type"`
	PeriodStart     string           `json:"period_start"`
	PeriodEnd       string           `json:"period_end"`
	ScoreStatus     string           `json:"score_status"`
	ScoreReason     string           `json:"score_reason"`
	Exact           *string          `json:"exact"`
	Score           *string          `json:"score"`
	Rating          *string          `json:"rating"`
	PolicyVersions  Versions         `json:"policy_versions"`
	Dimensions      []Dimension      `json:"dimensions"`
	Items           []Item           `json:"items"`
	Limitations     []string         `json:"limitations"`
	SourceDigest    string           `json:"source_digest"`
	GapDispositions []GapDisposition `json:"gap_dispositions"`
	Coverage        []Coverage       `json:"coverage"`
	Assessment      *Assessment      `json:"assessment,omitempty"`
	Eligibility     *Eligibility     `json:"eligibility,omitempty"`
	WeeklyReports   []Result         `json:"weekly_reports,omitempty"`
	WorkdayScope    *WorkdayScope    `json:"workday_scope,omitempty"`
}
type Bundle struct {
	SchemaVersion int      `json:"schema_version"`
	SourceName    string   `json:"source_name"`
	Reports       []Result `json:"reports"`
}
type Report struct {
	Result
	ScoreExplanation  *ScoreExplanation `json:"score_explanation,omitempty"`
	ID                int64             `json:"id"`
	UserID            int64             `json:"user_id"`
	Revision          int               `json:"revision"`
	PublicationStatus string            `json:"publication_status"`
	PublishedAt       *time.Time        `json:"published_at"`
	CreatedAt         time.Time         `json:"created_at"`
}
type Run struct {
	ID          int64     `json:"id"`
	Digest      string    `json:"digest"`
	SourceName  string    `json:"source_name"`
	ReportCount int       `json:"report_count"`
	CreatedAt   time.Time `json:"created_at"`
}
type Filter struct {
	UserID                                  int64
	PeriodType, PeriodStart, Status, Search string
	Page, PageSize                          int
}

var itemNames = map[string]string{"P1": "目标清楚", "P2": "信息充分", "P3": "要求约束", "M1": "方法分工", "M2": "依赖推进", "M3": "调整执行", "J1": "判断依据", "J2": "核验安排", "J3": "结果处理", "E1": "交互组织", "E2": "失效止损", "E3": "复用自动化"}
var dimensionNames = map[string]string{"P": "提示词质量", "M": "任务驾驭度", "J": "判断核验", "E": "使用效率"}
var weights = map[string]int64{"P": 20, "M": 35, "J": 30, "E": 15}
var sourceRefPattern = regexp.MustCompile(`^[a-zA-Z0-9:_-]{1,240}$`)
var credentialPattern = regexp.MustCompile(`(?i)(?:\bsk-[A-Za-z0-9_-]{16,}|(?:api[_-]?key|authorization|password|secret|token)["\s:=]+["']?[A-Za-z0-9+/=_-]{16,})`)
var numberPattern = regexp.MustCompile(`^[0-9]{1,30}(?:/[1-9][0-9]{0,29}|\.[0-9]{1,20})?$`)

func rational(s string) (*big.Rat, error) {
	// 限制有理数字符数，避免不可信导入制造超大整数。
	if len(s) > 64 || !numberPattern.MatchString(s) {
		return nil, invalid("数值过长")
	}
	n, ok := new(big.Rat).SetString(s)
	if !ok || n.Sign() < 0 || n.Cmp(big.NewRat(100, 1)) > 0 {
		return nil, invalid("分数须在0至100之间")
	}
	return n, nil
}
func display(n *big.Rat) *string {
	if n == nil {
		return nil
	}
	// 非负有理数精确 HALF_UP，避免float造成边界误差。
	num := new(big.Int).Mul(n.Num(), big.NewInt(10))
	q, r := new(big.Int), new(big.Int)
	q.QuoRem(num, n.Denom(), r)
	if r.Mul(r, big.NewInt(2)).Cmp(n.Denom()) >= 0 {
		q.Add(q, big.NewInt(1))
	}
	v := q.Int64()
	s := fmt.Sprintf("%d.%d", v/10, v%10)
	return &s
}
func exact(n *big.Rat) *string {
	if n == nil {
		return nil
	}
	s := n.RatString()
	return &s
}
func hashValid(s string) bool {
	if len(s) != 64 {
		return false
	}
	for _, c := range s {
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
			return false
		}
	}
	return true
}
func date(s string) (time.Time, error) {
	t, e := time.Parse("2006-01-02", s)
	if e != nil {
		return t, invalid("周期须为YYYY-MM-DD")
	}
	return t, nil
}
func level(n *big.Rat) *string {
	if n == nil {
		return nil
	}
	s := "L1"
	for i, b := range []int64{60, 70, 80, 90} {
		if n.Cmp(big.NewRat(b, 1)) >= 0 {
			s = fmt.Sprintf("L%d", i+2)
		}
	}
	return &s
}

// Validate 只接受登记版本；后续月评修改须新增版本处理，不重解释旧报告。
// 总分、维度和等级全部由服务端精确重算，忽略传入的展示值。
func (r *Result) Validate() error {
	if strings.TrimSpace(r.UserEmail) == "" || len(r.UserEmail) > 320 || !hashValid(r.SourceDigest) {
		return invalid("缺少明确的账户邮箱或来源哈希")
	}
	qualified := r.PolicyVersions.Rules == "1.4" || r.PolicyVersions.Rules == "1.5" || r.PolicyVersions.Rules == RulesVersion
	modern := qualified || r.PolicyVersions.Rules == "1.3"
	if r.PolicyVersions.Scale != ScaleVersion || (r.PolicyVersions.Rules != "1.1" && r.PolicyVersions.Rules != "1.2" && !modern) || r.PolicyVersions.Rating != "G2-proposal28" {
		return invalid("评分或规则版本未登记")
	}
	if r.PolicyVersions.Rules == RulesVersion && r.PolicyVersions.Review != ReviewPolicyVersion {
		return invalid("当前规则缺少对应语义评审版本，旧结果不能只改版本号升级")
	}
	a, e := date(r.PeriodStart)
	if e != nil {
		return e
	}
	b, e := date(r.PeriodEnd)
	if e != nil {
		return e
	}
	closed := b.Add(-8 * time.Hour)
	if r.WorkdayScope != nil {
		if r.PolicyVersions.Scope != WorkdayScopeVersion {
			return invalid("工作日范围缺少对应版本")
		}
		if e := r.WorkdayScope.validate(r.PeriodStart, r.PeriodEnd); e != nil {
			return e
		}
		closed, _ = time.Parse(time.RFC3339, r.WorkdayScope.FreezeAt)
		for _, item := range r.Items {
			for _, ev := range item.Evidence {
				if ev.Role == "user" && !r.WorkdayScope.contains(ev.OccurredAt) {
					return invalid("非工作日本人输入不能作为本期表现依据")
				}
			}
		}
	} else if r.PolicyVersions.Scope != "" {
		return invalid("范围版本没有对应冻结工作日范围")
	}
	if !a.Before(b) || closed.After(time.Now()) {
		return invalid("周期未结束或范围无效")
	}
	switch r.PeriodType {
	case "week":
		if a.Weekday() != time.Monday || b.Sub(a) != 7*24*time.Hour || r.PolicyVersions.Aggregation != ScaleVersion {
			return invalid("周报告必须是完整自然周并使用023汇总")
		}
	case "month":
		wantAggregation := MonthVersion
		if qualified {
			wantAggregation = QualifiedMonthVersion
		}
		if a.Day() != 1 || b != a.AddDate(0, 1, 0) || r.PolicyVersions.Aggregation != wantAggregation {
			return invalid("月报告必须是完整自然月并使用024汇总")
		}
	default:
		return invalid("未知周期类型")
	}
	if r.PeriodType == "month" && len(r.Items) > 0 {
		cursor := a
		for _, c := range r.Coverage {
			x, e := date(c.PeriodStart)
			if e != nil {
				return e
			}
			y, e := date(c.PeriodEnd)
			if e != nil {
				return e
			}
			want := x.AddDate(0, 0, 7-(int(x.Weekday())+6)%7)
			if want.After(b) {
				want = b
			}
			if x != cursor || y != want || !c.EvidenceChecked {
				return invalid("月度来源须覆盖完整月份，且逐期间核完证据")
			}
			cursor = y
		}
		if cursor != b {
			return invalid("月度来源尚未覆盖完整月份")
		}
	}
	validWeeks := map[string]bool{}
	if qualified {
		var err error
		validWeeks, err = r.validateWeeklyReports()
		if err != nil {
			return err
		}
	} else if len(r.WeeklyReports) != 0 {
		return invalid("历史合同不能携带新周评来源")
	}
	blocked := false
	if r.Assessment != nil {
		a := r.Assessment
		wantVersion := AssessmentVersion
		if modern {
			wantVersion = ObservationAssessmentVersion
		}
		if r.PeriodType != "month" || a.Version != wantVersion || !hashValid(a.SourceGapDigest) || strings.TrimSpace(a.Scope) == "" {
			return invalid("资料处置版本或范围无效")
		}
		ids := map[string]bool{}
		for _, issue := range a.Issues {
			if !hashValid(issue.ID) || ids[issue.ID] || strings.TrimSpace(issue.Reason) == "" || strings.TrimSpace(issue.Description) == "" || issue.Status != "blocking" && issue.Status != "limitation" {
				return invalid("资料提示处置重复或缺少明确依据")
			}
			if credentialPattern.MatchString(issue.Description) || credentialPattern.MatchString(issue.Reason) {
				return invalid("资料提示含未脱敏凭证")
			}
			ids[issue.ID] = true
			blocked = blocked || issue.Status == "blocking"
		}
	}
	if r.ScoreStatus != "scored" && r.ScoreStatus != "no_activity" && r.ScoreStatus != "insufficient_evidence" && !(modern && (r.ScoreStatus == "observation_only" || r.ScoreStatus == "data_pending")) {
		return invalid("未知评分状态")
	}
	r.Exact = nil
	r.Score = nil
	r.Rating = nil
	r.Dimensions = []Dimension{}
	if r.ScoreStatus != "scored" && !(modern && len(r.Items) > 0) {
		if strings.TrimSpace(r.ScoreReason) == "" || len(r.Items) > 0 {
			return invalid("无完整评分的报告须有原因且不携带数值分项")
		}
		if modern {
			if e := r.validateEligibility(); e != nil {
				return e
			}
			if r.Eligibility.Eligible || r.ScoreStatus == "no_activity" && (r.Eligibility.TaskCount != 0 || !r.Eligibility.SourcesComplete) || r.ScoreStatus == "data_pending" && r.Eligibility.SourcesComplete {
				return invalid("空报告状态与实际观察不一致")
			}
		}
		return nil
	}
	if len(r.Items) != 12 {
		return invalid("评分报告须包含完整12项")
	}
	seen := map[string]bool{}
	values := map[string][]*big.Rat{}
	gaps := map[string][]*big.Rat{}
	dispositions := map[string]GapDisposition{}
	for _, g := range r.GapDispositions {
		key := g.ItemCode + "/" + g.PeriodStart
		if _, ok := dispositions[key]; ok || g.Reason == "" || g.Status != "open" && g.Status != "recovered" {
			return invalid("缺口处置重复或不完整")
		}
		dispositions[key] = g
	}
	for i := range r.Items {
		it := &r.Items[i]
		if credentialPattern.MatchString(it.Reason) || credentialPattern.MatchString(it.Criterion) {
			return invalid("判定内容含未脱敏凭证，请处理后重新导出")
		}
		name, ok := itemNames[it.Code]
		if !ok || seen[it.Code] {
			return invalid("分项缺失或重复")
		}
		seen[it.Code] = true
		it.Name = name
		if r.PeriodType == "month" {
			if len(it.PeriodStates) != len(r.Coverage) {
				return invalid("月分项须保留每个评价期间的状态")
			}
			numeric := map[string]bool{}
			for _, c := range it.Components {
				numeric[c.PeriodStart] = true
			}
			for _, c := range r.Coverage {
				state, ok := it.PeriodStates[c.PeriodStart]
				if qualified && !validWeeks[c.PeriodStart] {
					if !ok || state != "excluded_week" || numeric[c.PeriodStart] {
						return invalid("无正式周评的期间不得进入月分")
					}
					continue
				}
				if qualified {
					for _, w := range r.WeeklyReports {
						if w.PeriodStart == c.PeriodStart && w.PeriodEnd == c.PeriodEnd {
							for _, wi := range w.Items {
								if wi.Code == it.Code && wi.State != state {
									return invalid("月分项状态与正式周评不一致")
								}
							}
						}
					}
				}
				unresolved := state == "unobservable" || state == "attribution_unclear" || state == "pending"
				if !ok || state != "observed" && state != "no_opportunity" && state != "related" && state != "not_applicable" && !(r.Assessment != nil && unresolved) {
					return invalid("月分项存在未完成、不可观察或缺失期间")
				}
				if unresolved && (!modern || state != "unobservable" || it.Exact == nil) {
					blocked = true
				}
				if (state == "observed") != numeric[c.PeriodStart] {
					return invalid("月分项数值与期间状态不一致")
				}
			}
		}
		if strings.TrimSpace(it.Reason) == "" {
			return invalid(it.Code + "缺少判定理由")
		}
		it.Score = nil
		if it.State != "observed" {
			if it.State != "no_opportunity" && it.State != "unobservable" && it.State != "attribution_unclear" && it.State != "pending" && it.State != "related" && it.State != "not_applicable" && !(r.PeriodType == "month" && (it.State == "mixed_unscored" || qualified && it.State == "excluded_week")) {
				return invalid("未知分项状态")
			}
			if it.Exact != nil || it.NecessaryGap || len(it.Components) > 0 {
				return invalid("未评分项不能补数")
			}
			continue
		}
		if it.Exact == nil || len(it.Evidence) == 0 || strings.TrimSpace(it.Criterion) == "" {
			return invalid(it.Code + "缺少分数、判据或实际依据")
		}
		n, e := rational(*it.Exact)
		if e != nil {
			return e
		}
		if r.PeriodType == "week" {
			allowed := false
			for _, v := range []int64{0, 20, 40, 60, 70, 80, 90, 100} {
				if n.Cmp(big.NewRat(v, 1)) == 0 {
					allowed = true
				}
			}
			if !allowed || len(it.Components) > 0 {
				return invalid("周分项须使用023表现档")
			}
			if it.NecessaryGap != (n.Cmp(big.NewRat(40, 1)) <= 0) {
				return invalid("0/20/40档必须保留必要缺口标记")
			}
		} else {
			sum := new(big.Rat)
			days := int64(0)
			last := a
			if len(it.Components) == 0 {
				return invalid("月分项缺少期间计算来源")
			}
			for _, c := range it.Components {
				x, e := date(c.PeriodStart)
				if e != nil {
					return e
				}
				y, e := date(c.PeriodEnd)
				if e != nil {
					return e
				}
				// 每段必须是自然周与月份的交集；可评项缺段不补数。
				monday := x.AddDate(0, 0, -(int(x.Weekday())+6)%7)
				wantStart := monday
				if wantStart.Before(a) {
					wantStart = a
				}
				wantEnd := monday.AddDate(0, 0, 7)
				if wantEnd.After(b) {
					wantEnd = b
				}
				if x != wantStart || y != wantEnd || x.Before(last) || !x.Before(y) {
					return invalid("月分项来源存在重叠、跨月或非自然周片段")
				}
				last = y
				v, e := rational(c.Exact)
				if e != nil {
					return e
				}
				if qualified {
					if !validWeeks[c.PeriodStart] {
						return invalid("未合格周的数值不得进入月分")
					}
					if err := r.checkWeeklyComponent(it.Code, c); err != nil {
						return err
					}
				}
				valid := false
				for _, s := range []int64{0, 20, 40, 60, 70, 80, 90, 100} {
					if v.Cmp(big.NewRat(s, 1)) == 0 {
						valid = true
					}
				}
				if !valid {
					return invalid("月来源须使用023表现档")
				}
				if c.NecessaryGap != (v.Cmp(big.NewRat(40, 1)) <= 0) {
					return invalid("月来源0/20/40档必须保留必要缺口标记")
				}
				if !hasPersonEvidence(it.Evidence, x, y) {
					return invalid("月分项的每个已评分期间须有当期本人证据")
				}
				if c.NecessaryGap {
					if v.Cmp(big.NewRat(40, 1)) > 0 {
						return invalid("必要缺口来源分数无效")
					}
					key := it.Code + "/" + c.PeriodStart
					g, ok := dispositions[key]
					if !ok {
						return invalid("月末必要缺口尚未处置")
					}
					delete(dispositions, key)
					if g.Status == "open" {
						gaps[it.Code[:1]] = append(gaps[it.Code[:1]], v)
					} else {
						recovered := false
						for _, rc := range it.Components {
							if rc.PeriodStart == g.RecoveryPeriod && rc.PeriodStart >= c.PeriodEnd && !rc.NecessaryGap {
								recovered = true
							}
						}
						refs := map[string]bool{}
						for _, rc := range it.Components {
							if rc.PeriodStart != g.RecoveryPeriod {
								continue
							}
							x, _ := date(rc.PeriodStart)
							y, _ := date(rc.PeriodEnd)
							for _, ev := range it.Evidence {
								if hasPersonEvidence([]Evidence{ev}, x, y) {
									refs[ev.ID] = true
								}
							}
						}
						if !recovered || len(g.RecoveryEvidenceIDs) == 0 {
							return invalid("恢复须引用后续月内期间与实际证据")
						}
						for _, ref := range g.RecoveryEvidenceIDs {
							if !refs[ref] {
								return invalid("恢复引用未绑定后续证据")
							}
						}
					}
				}
				d := int64(y.Sub(x) / (24 * time.Hour))
				days += d
				sum.Add(sum, new(big.Rat).Mul(v, big.NewRat(d, 1)))
			}
			if days == 0 || new(big.Rat).Quo(sum, big.NewRat(days, 1)).Cmp(n) != 0 {
				return invalid("月分项与覆盖时段汇总不一致")
			}
		}
		if it.NecessaryGap && (r.PeriodType != "week" || n.Cmp(big.NewRat(40, 1)) > 0) {
			return invalid("必要缺口仅适用于周期间已证的0/20/40档")
		}
		if !hasPersonEvidence(it.Evidence, a, b) {
			return invalid(it.Code + "缺少本期本人表现证据")
		}
		refs := map[string]bool{}
		for _, ev := range it.Evidence {
			if !sourceRefPattern.MatchString(ev.SourceRef) || credentialPattern.MatchString(ev.TaskTitle) || credentialPattern.MatchString(ev.Quote) || credentialPattern.MatchString(ev.Context) || credentialPattern.MatchString(ev.Supports) {
				return invalid("证据定位无效或包含未脱敏凭证")
			}
			if ev.ID == "" || refs[ev.ID] || ev.SourceRef == "" || !hashValid(ev.SourceHash) || ev.TaskTitle == "" || ev.Quote == "" || ev.Supports == "" || ev.OccurredAt.IsZero() || ev.Role != "user" && ev.Role != "assistant" && ev.Role != "tool" && ev.Role != "context" {
				return invalid(it.Code + "证据字段缺失或重复")
			}
			if ev.Origin != "direct_expression" && ev.Origin != "explicit_adoption" && ev.Origin != "quoted_or_forwarded" && ev.Origin != "uncertain" && ev.Origin != "context" {
				return invalid("证据须保留本人归属状态")
			}
			refs[ev.ID] = true
			// 时间按北京时间归属；上下文可早于本期，不得引用未来材料。
			if !ev.OccurredAt.Before(b.Add(-8 * time.Hour)) {
				return invalid("证据晚于报告截止点")
			}
			if (ev.Origin == "direct_expression" || ev.Origin == "explicit_adoption") && ev.OccurredAt.Before(a.Add(-8*time.Hour)) {
				return invalid("本人表现证据早于本期，历史仅可作背景")
			}
			if !strings.Contains(ev.Context, ev.Quote) {
				return invalid("引用不在冻结上下文中")
			}
			if len(ev.Context) > 16000 || len(ev.Quote) > 8000 {
				return invalid("证据上下文过长，请仅保留相关内容")
			}
		}
		it.Exact = exact(n)
		it.Score = display(n)
		d := it.Code[:1]
		values[d] = append(values[d], n)
		if it.NecessaryGap {
			gaps[d] = append(gaps[d], n)
		}
	}
	if len(dispositions) > 0 {
		return invalid("缺口处置引用不存在的必要缺口")
	}
	total := new(big.Rat)
	complete := true
	for _, d := range []string{"P", "M", "J", "E"} {
		var v *big.Rat
		if len(gaps[d]) > 0 {
			v = new(big.Rat).Set(gaps[d][0])
			for _, g := range gaps[d][1:] {
				if g.Cmp(v) < 0 {
					v.Set(g)
				}
			}
		} else if len(values[d]) > 0 {
			v = new(big.Rat)
			for _, x := range values[d] {
				v.Add(v, x)
			}
			v.Quo(v, big.NewRat(int64(len(values[d])), 1))
		}
		r.Dimensions = append(r.Dimensions, Dimension{d, dimensionNames[d], exact(v), display(v)})
		if v == nil {
			complete = false
		} else {
			total.Add(total, new(big.Rat).Mul(v, big.NewRat(weights[d], 100)))
		}
	}
	if complete {
		r.Exact = exact(total)
		r.Score = display(total)
		if r.PeriodType == "month" && !blocked {
			r.Rating = level(total)
		}
	} else if strings.TrimSpace(r.ScoreReason) == "" {
		return invalid("缺维度时须说明总分缺失原因")
	}
	if blocked {
		r.ScoreReason = "本月部分资料不可观察或判断未决。分数仅覆盖当前可评范围，暂不形成完整月度等级；未知部分未补分。"
	}
	if modern {
		if e := r.validateEligibility(); e != nil {
			return e
		}
		if qualified && blocked {
			r.Eligibility.Eligible = false
			r.Eligibility.Reasons = append(r.Eligibility.Reasons, "存在尚未解决的关键资料或判断问题")
		}
		r.ScoreStatus = "scored"
		if !r.Eligibility.Eligible {
			r.ScoreStatus = "observation_only"
			if !r.Eligibility.SourcesComplete {
				r.ScoreStatus = "data_pending"
			}
			r.Exact, r.Score, r.Rating = nil, nil, nil
			r.ScoreReason = strings.Join(r.Eligibility.Reasons, "；") + "。保留已核分项和维度观察，暂不形成完整周期综合分。"
		}
	}
	return nil
}

// 日期字段代表北京时间零点，历史与转发材料不充当本期本人表现。
func hasPersonEvidence(evidence []Evidence, start, end time.Time) bool {
	for _, ev := range evidence {
		if (ev.Origin == "direct_expression" || ev.Origin == "explicit_adoption") &&
			!ev.OccurredAt.Before(start.Add(-8*time.Hour)) && ev.OccurredAt.Before(end.Add(-8*time.Hour)) {
			return true
		}
	}
	return false
}
