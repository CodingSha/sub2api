package zhiyongli

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"
	"time"
)

const EligibilityVersion = "eligibility-20261106.1"
const QualifiedEligibilityVersion = "eligibility-20261106.2"
const CurrentEligibilityVersion = "eligibility-20261109.3"
const ObservationAssessmentVersion = "scope-disclosure-20261106.2"

type Observation struct {
	SourceRef  string    `json:"source_ref"`
	SourceHash string    `json:"source_hash"`
	OccurredAt time.Time `json:"occurred_at"`
	TaskKeys   []string  `json:"task_keys"`
}
type DimensionCoverage struct {
	Code   string `json:"code"`
	Events int    `json:"events"`
	Tasks  int    `json:"tasks"`
	Days   int    `json:"days"`
	Weeks  int    `json:"weeks"`
}
type Eligibility struct {
	Version           string              `json:"version"`
	SourceDigest      string              `json:"source_digest"`
	SourcesComplete   bool                `json:"sources_complete"`
	Observations      []Observation       `json:"observations"`
	DimensionRefs     map[string][]string `json:"dimension_refs"`
	Eligible          bool                `json:"eligible"`
	Route             string              `json:"route"`
	Reasons           []string            `json:"reasons"`
	TaskCount         int                 `json:"task_count"`
	ActiveDays        int                 `json:"active_days"`
	ActiveWeeks       int                 `json:"active_weeks"`
	DimensionCoverage []DimensionCoverage `json:"dimension_coverage"`
	ValidWeeks        int                 `json:"valid_weeks,omitempty"`
	TotalWeeks        int                 `json:"total_weeks,omitempty"`
}

var observationTZ = time.FixedZone("Asia/Shanghai", 8*60*60)

func observationDigest(g *Eligibility) string {
	rows := []map[string]any{}
	for _, e := range g.Observations {
		keys := append([]string{}, e.TaskKeys...)
		sort.Strings(keys)
		rows = append(rows, map[string]any{"source_ref": e.SourceRef, "source_hash": e.SourceHash, "date": observationDate(e.OccurredAt), "task_keys": keys})
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i]["source_ref"].(string) < rows[j]["source_ref"].(string) })
	refs := map[string][]string{}
	for d, source := range g.DimensionRefs {
		refs[d] = append([]string{}, source...)
		sort.Strings(refs[d])
	}
	raw, _ := json.Marshal(map[string]any{"observations": rows, "dimension_refs": refs})
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}

func observationDate(at time.Time) string { return at.In(observationTZ).Format("2006-01-02") }
func observationWeek(at time.Time) string {
	t := at.In(observationTZ)
	return t.AddDate(0, 0, -(int(t.Weekday())+6)%7).Format("2006-01-02")
}
func observationCoverage(code string, events []Observation) DimensionCoverage {
	dates, tasks, weeks := map[string]bool{}, map[string]bool{}, map[string]bool{}
	for _, e := range events {
		dates[observationDate(e.OccurredAt)] = true
		weeks[observationWeek(e.OccurredAt)] = true
		for _, key := range e.TaskKeys {
			tasks[key] = true
		}
	}
	return DimensionCoverage{code, len(events), len(tasks), len(dates), len(weeks)}
}

// 资格来自已核输入的出处，不信任导入方的布尔值或数量；不调整分项及维度分。
func (r *Result) validateEligibility() error {
	g := r.Eligibility
	current := r.PolicyVersions.Rules == "1.4" || r.PolicyVersions.Rules == "1.5" || r.PolicyVersions.Rules == RulesVersion
	withoutTaskGate := r.PolicyVersions.Rules == "1.5" || r.PolicyVersions.Rules == RulesVersion
	wantVersion := EligibilityVersion
	if current {
		wantVersion = QualifiedEligibilityVersion
	}
	if withoutTaskGate {
		wantVersion = CurrentEligibilityVersion
	}
	if g == nil || g.Version != wantVersion || !hashValid(g.SourceDigest) || len(g.Observations) > 40000 || len(g.DimensionRefs) != 4 {
		return invalid("观察资格版本、签名或四维来源缺失")
	}
	if observationDigest(g) != g.SourceDigest {
		return invalid("观察来源签名与事件、日期或任务集合不一致")
	}
	a, _ := date(r.PeriodStart)
	b, _ := date(r.PeriodEnd)
	byRef, byTask := map[string]Observation{}, map[string][]Observation{}
	for _, e := range g.Observations {
		if r.WorkdayScope != nil && !r.WorkdayScope.contains(e.OccurredAt) {
			return invalid("观察来源包含非计分日期的本人行为")
		}
		if !sourceRefPattern.MatchString(e.SourceRef) || !hashValid(e.SourceHash) || e.OccurredAt.Before(a.Add(-8*time.Hour)) || !e.OccurredAt.Before(b.Add(-8*time.Hour)) || (len(e.TaskKeys) == 0 && r.PolicyVersions.Rules != RulesVersion) || len(e.TaskKeys) > 100 {
			return invalid("观察事件缺来源、任务定位或不在评价期间")
		}
		if _, ok := byRef[e.SourceRef]; ok {
			return invalid("同一本人事件不能重复增加观察数量")
		}
		byRef[e.SourceRef] = e
		keys := map[string]bool{}
		for _, key := range e.TaskKeys {
			if !sourceRefPattern.MatchString(key) || keys[key] {
				return invalid("观察任务定位无效或重复")
			}
			keys[key] = true
			byTask[key] = append(byTask[key], e)
		}
	}
	// 四维只认当前已评分项里的本人动作依据；背景材料不计M/J/E。
	allowed := map[string]map[string]bool{}
	for _, d := range []string{"P", "M", "J", "E"} {
		allowed[d] = map[string]bool{}
	}
	for _, it := range r.Items {
		if it.State != "observed" || it.Exact == nil {
			continue
		}
		d := it.Code[:1]
		for _, ev := range it.Evidence {
			person := ev.Origin == "direct_expression" || ev.Origin == "explicit_adoption" || d == "P" && ev.Origin == "quoted_or_forwarded"
			if e, ok := byRef[ev.SourceRef]; ok && person && ev.SourceHash == e.SourceHash && ev.OccurredAt.Equal(e.OccurredAt) {
				allowed[d][ev.SourceRef] = true
			}
		}
	}
	g.Reasons, g.DimensionCoverage = []string{}, []DimensionCoverage{}
	all := observationCoverage("", g.Observations)
	g.TaskCount, g.ActiveDays, g.ActiveWeeks = all.Tasks, all.Days, all.Weeks
	needTasks, needDays := 5, 3
	if r.PeriodType == "month" {
		needTasks, needDays = 20, 10
	}
	if current {
		needTasks, needDays = 20, 0
	}
	if !g.SourcesComplete {
		g.Reasons = append(g.Reasons, "来源或期间评测尚未完成")
	}
	if all.Days < needDays {
		g.Reasons = append(g.Reasons, fmt.Sprintf("本人记录%d天，至少需要%d天", all.Days, needDays))
	}
	if !current && r.PeriodType == "month" && all.Weeks < 3 {
		g.Reasons = append(g.Reasons, "本人记录未覆盖至少3个自然周")
	}
	refs := map[string]map[string]bool{}
	for _, d := range []string{"P", "M", "J", "E"} {
		input, ok := g.DimensionRefs[d]
		if !ok {
			return invalid("观察资格缺少明确的四维来源集合")
		}
		refs[d] = map[string]bool{}
		own := []Observation{}
		for _, ref := range input {
			if refs[d][ref] || !allowed[d][ref] {
				return invalid("分维观察引用重复或未绑定当期已核本人依据")
			}
			refs[d][ref] = true
			own = append(own, byRef[ref])
		}
		c := observationCoverage(d, own)
		g.DimensionCoverage = append(g.DimensionCoverage, c)
		enough := c.Events >= 2 && (c.Tasks >= 2 || c.Days >= 2)
		if r.PeriodType == "month" {
			enough = c.Weeks >= 2
		}
		if current {
			enough = c.Events > 0
		}
		if !enough {
			reason := d + "维度尚无两处不同任务或跨日的本人依据"
			if r.PeriodType == "month" {
				reason = d + "维度本人依据尚未覆盖2个自然周"
			}
			if current {
				reason = d + "维度尚无可评本人依据"
			}
			g.Reasons = append(g.Reasons, reason)
		}
	}
	g.Route = "standard"
	if current {
		g.ValidWeeks, g.TotalWeeks = 0, 0
		if !withoutTaskGate && r.PeriodType == "week" && all.Tasks < 20 {
			g.Route = "insufficient_task_coverage"
			g.Reasons = append(g.Reasons, fmt.Sprintf("本周独立任务%d个，未达到20个，暂不形成周评分", all.Tasks))
		}
		if r.PeriodType == "month" {
			weeks, err := r.validateWeeklyReports()
			if err != nil {
				return err
			}
			g.TotalWeeks = len(r.Coverage)
			for _, valid := range weeks {
				if valid {
					g.ValidWeeks++
				}
			}
			if g.ValidWeeks < 3 {
				g.Route = "insufficient_week_coverage"
				g.Reasons = append(g.Reasons, fmt.Sprintf("本月有效周评%d周，不足3周，暂不形成月评分及评级", g.ValidWeeks))
			}
		}
	} else if all.Tasks < needTasks {
		g.Route = "insufficient_task_coverage"
		keys := make([]string, 0, len(byTask))
		for key := range byTask {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for _, key := range keys {
			events := byTask[key]
			c := observationCoverage("", events)
			if c.Days < needDays || r.PeriodType == "month" && c.Weeks < 3 {
				continue
			}
			enough := true
			for _, d := range []string{"P", "M", "J", "E"} {
				own := []Observation{}
				for _, e := range events {
					if refs[d][e.SourceRef] {
						own = append(own, e)
					}
				}
				c := observationCoverage(d, own)
				enough = enough && (r.PeriodType == "week" && c.Days >= 2 || r.PeriodType == "month" && c.Weeks >= 2)
			}
			if enough {
				g.Route = "long_task"
				break
			}
		}
		if g.Route != "long_task" {
			g.Reasons = append(g.Reasons, fmt.Sprintf("任务%d个，至少需要%d个，且未满足持续任务通道", all.Tasks, needTasks))
		}
	}
	for _, d := range r.Dimensions {
		if d.Exact == nil {
			g.Reasons = append(g.Reasons, d.Code+"维度没有可评数值")
		}
	}
	if current {
		for _, it := range r.Items {
			if it.State == "pending" || it.State == "attribution_unclear" {
				g.Reasons = append(g.Reasons, "存在待复核或本人归属未决的判断")
				break
			}
		}
	}
	g.Eligible = len(g.Reasons) == 0
	return nil
}
