package zhiyongli

import (
	"reflect"
	"time"
)

// 月资格只接受可重算的同版完整周；月内片段自身不能冒充正式周评。
func (r *Result) validateWeeklyReports() (map[string]bool, error) {
	valid := map[string]bool{}
	if r.PeriodType != "month" {
		if len(r.WeeklyReports) != 0 {
			return nil, invalid("周报告不能嵌套周报告")
		}
		return valid, nil
	}
	if len(r.WeeklyReports) > 6 || r.Eligibility == nil {
		return nil, invalid("月周评来源过多或缺少观察来源")
	}
	start, _ := date(r.PeriodStart)
	end, _ := date(r.PeriodEnd)
	monthEvents := map[string]Observation{}
	for _, ev := range r.Eligibility.Observations {
		monthEvents[ev.SourceRef] = ev
	}
	seen := map[string]bool{}
	for i := range r.WeeklyReports {
		w := &r.WeeklyReports[i]
		if w.PolicyVersions.Scope != r.PolicyVersions.Scope || (w.WorkdayScope == nil) != (r.WorkdayScope == nil) ||
			(w.WorkdayScope != nil && (w.WorkdayScope.RegistrySHA != r.WorkdayScope.RegistrySHA || w.WorkdayScope.CalendarSHA != r.WorkdayScope.CalendarSHA)) {
			return nil, invalid("月周评不能混用自然周、工作日或不同日历范围")
		}
		if len(w.WeeklyReports) != 0 || w.PeriodType != "week" || w.UserEmail != r.UserEmail || w.PolicyVersions.Rules != r.PolicyVersions.Rules || w.PolicyVersions.Scale != r.PolicyVersions.Scale || w.PolicyVersions.Rating != r.PolicyVersions.Rating || seen[w.PeriodStart] {
			return nil, invalid("月周评来源嵌套、重复、异人或版本不一致")
		}
		if err := w.Validate(); err != nil {
			return nil, err
		}
		a, _ := date(w.PeriodStart)
		b, _ := date(w.PeriodEnd)
		if !a.Before(end) || !start.Before(b) {
			return nil, invalid("周评来源与本月不相交")
		}
		seen[w.PeriodStart] = true
		key := w.PeriodStart
		if a.Before(start) {
			key = r.PeriodStart
		}
		if r.Eligibility.SourcesComplete && !w.Eligibility.SourcesComplete {
			return nil, invalid("周来源未完成，月来源不能声明完成")
		}
		weekEvents := map[string]Observation{}
		for _, ev := range w.Eligibility.Observations {
			if ev.OccurredAt.Before(start.Add(-8*time.Hour)) || !ev.OccurredAt.Before(end.Add(-8*time.Hour)) {
				continue
			}
			weekEvents[ev.SourceRef] = ev
			other, ok := monthEvents[ev.SourceRef]
			if !ok || !sameObservation(ev, other) {
				return nil, invalid("正式周评与月内来源不一致")
			}
		}
		for _, ev := range r.Eligibility.Observations {
			if !ev.OccurredAt.Before(a.Add(-8*time.Hour)) && ev.OccurredAt.Before(b.Add(-8*time.Hour)) {
				if _, ok := weekEvents[ev.SourceRef]; !ok {
					return nil, invalid("月来源未完整绑定对应周评")
				}
			}
		}
		valid[key] = w.ScoreStatus == "scored" && w.Score != nil && w.Eligibility.Eligible
		// 不能通过剔除有争议周来绕过关键未决；任务量不足单独作为资格问题。
		for _, it := range w.Items {
			if it.State == "pending" || it.State == "attribution_unclear" {
				return nil, invalid("周评关键判断未决，月评须先处理")
			}
		}
	}
	return valid, nil
}

func sameObservation(a, b Observation) bool {
	return a.SourceHash == b.SourceHash && a.OccurredAt.Equal(b.OccurredAt) && reflect.DeepEqual(taskSet(a.TaskKeys), taskSet(b.TaskKeys))
}

func taskSet(keys []string) map[string]bool {
	r := map[string]bool{}
	for _, k := range keys {
		r[k] = true
	}
	return r
}

func (r *Result) checkWeeklyComponent(code string, c Component) error {
	for _, w := range r.WeeklyReports {
		// 跨月周须另判月内片段，不把整周数值机械覆盖到片段。
		if w.PeriodStart != c.PeriodStart || w.PeriodEnd != c.PeriodEnd {
			continue
		}
		for _, it := range w.Items {
			if it.Code != code {
				continue
			}
			v, err := rational(c.Exact)
			if err != nil {
				return err
			}
			if it.Exact == nil {
				return invalid("月数值分项在正式周评中无数值")
			}
			wv, err := rational(*it.Exact)
			if err != nil {
				return err
			}
			if v.Cmp(wv) != 0 || c.NecessaryGap != it.NecessaryGap {
				return invalid("月分项与正式周评不一致")
			}
			return nil
		}
		return invalid("正式周评缺少对应分项")
	}
	return nil
}
