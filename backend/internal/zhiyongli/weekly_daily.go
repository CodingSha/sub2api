package zhiyongli

import (
	"context"
	"database/sql"
	"encoding/json"
	"reflect"
	"strings"
	"time"
)

const WeeklyDailyVersion = "weekly-from-daily-20261122.1"
const WeeklyDailyAggregation = "weekly-complete-dimensions-20261122.2"

// DailyReportSource 绑定同人每日当前已发布修订；日分不参与周分计算。
type DailyReportSource struct {
	ReportID      int64  `json:"report_id"`
	PeriodStart   string `json:"period_start"`
	SourceDigest  string `json:"source_digest"`
	RequestDigest string `json:"request_digest"`
}

type WeeklyReview struct {
	DailyReview
	Sources []DailyReportSource `json:"sources"`
}

func (r *Result) validateWeeklyReview() error {
	w := r.WeeklyReview
	if r.PeriodType != "week" || r.DailyReview != nil || w == nil || w.Version != WeeklyDailyVersion || w.Model != "deepseek-v4.1-flash-local" || !hashValid(w.RequestDigest) || !hashValid(r.SourceDigest) || !strings.Contains(r.UserEmail, "@") {
		return invalid("新版周评缺少明确人员、冻结日评来源或模型回执")
	}
	p := r.PolicyVersions
	if p.Scale != ScaleVersion || p.Rules != RulesVersion || p.Review != WeeklyDailyVersion || p.Aggregation != WeeklyDailyAggregation || p.Scope != WorkdayScopeVersion || p.Rating != "G2-proposal28" {
		return invalid("新版周评版本未登记")
	}
	a, err := date(r.PeriodStart)
	if err != nil || a.Weekday() != time.Monday || r.PeriodEnd != a.AddDate(0, 0, 7).Format("2006-01-02") || r.WorkdayScope == nil {
		return invalid("新版周评须绑定完整工作周范围")
	}
	if err := r.WorkdayScope.validate(r.PeriodStart, r.PeriodEnd); err != nil {
		return err
	}
	dates := r.WorkdayScope.WorkingDates
	if len(dates) == 0 || len(w.Sources) != len(dates) || len(dates) > 7 || len(r.WorkdayScope.EvaluationDates) != 0 || w.WindowStart != dates[0]+"T08:00:00+08:00" || w.WindowEnd != dates[len(dates)-1]+"T18:00:00+08:00" {
		return invalid("新版周评必须覆盖本周全部工作日的已发布日评")
	}
	for i, source := range w.Sources {
		if source.ReportID <= 0 || source.PeriodStart != dates[i] || !hashValid(source.SourceDigest) || !hashValid(source.RequestDigest) {
			return invalid("新版周评日评来源缺失、重复或签名无效")
		}
	}
	freeze, err := time.Parse(time.RFC3339, r.WorkdayScope.FreezeAt)
	if err != nil || freeze.After(time.Now()) {
		return invalid("工作周来源尚未封口")
	}
	if r.TaskReview != nil || r.SampleInitial != nil || r.Eligibility != nil || r.Assessment != nil || len(r.Coverage) > 0 || len(r.WeeklyReports) > 0 || len(r.GapDispositions) > 0 {
		return invalid("新版周评不能混入旧周评、日分平均或月评级来源")
	}
	return r.validateTaskEvidence(&w.DailyReview, 5*len(dates), 160000*len(dates), 40*len(dates), func(at time.Time) bool {
		for _, day := range dates {
			start, _ := time.Parse(time.RFC3339, day+"T08:00:00+08:00")
			if !at.Before(start) && at.Before(start.Add(10*time.Hour)) {
				return true
			}
		}
		return false
	})
}

// weeklyPrefix 仅给跨日编号加命名空间，正文及原文哈希保持不变。
func weeklyPrefix(day string) string { return "d" + strings.ReplaceAll(day, "-", "") + "-" }

func (r *Result) validatePublishedDays(ctx context.Context, tx *sql.Tx, uid int64) error {
	if r.WeeklyReview == nil {
		return nil
	}
	tasks := map[string]DailyTask{}
	behaviors := map[string]DailyBehavior{}
	people := map[string]bool{}
	for _, source := range r.WeeklyReview.Sources {
		var raw []byte
		err := tx.QueryRowContext(ctx, `SELECT body FROM zhiyongli_reports WHERE id=$1 AND user_id=$2 AND period_type='day' AND period_start=$3::date AND publication_status='published' FOR SHARE`, source.ReportID, uid, source.PeriodStart).Scan(&raw)
		if err != nil {
			return invalid("周评来源日评已变化或尚未发布，请核对来源版本")
		}
		var day Result
		if json.Unmarshal(raw, &day) != nil || day.DailyReview == nil || day.DailyReview.Version != DailyReviewVersion || day.SourceDigest != source.SourceDigest || day.DailyReview.RequestDigest != source.RequestDigest {
			return invalid("周评来源日评签名不一致")
		}
		prefix := weeklyPrefix(source.PeriodStart)
		for key, task := range day.DailyReview.Conversations {
			for i := range task.Records {
				record := &task.Records[i]
				if record.Role == "user" {
					if people[record.ID] {
						return invalid("跨日日评含同一用户记录，须先核对去重")
					}
					people[record.ID] = true
				}
				record.ID = prefix + record.ID
			}
			for i := range task.ToolIndex {
				task.ToolIndex[i].RecordID = prefix + task.ToolIndex[i].RecordID
			}
			for i := range task.Workflow {
				for j := range task.Workflow[i].UserRefs {
					task.Workflow[i].UserRefs[j] = prefix + task.Workflow[i].UserRefs[j]
				}
				for j := range task.Workflow[i].ToolRefs {
					task.Workflow[i].ToolRefs[j] = prefix + task.Workflow[i].ToolRefs[j]
				}
			}
			tasks[prefix+key] = task
		}
		for key, behavior := range day.DailyReview.Behaviors {
			behavior.SourceRef = prefix + behavior.SourceRef
			behaviors[prefix+key] = behavior
		}
	}
	if !reflect.DeepEqual(tasks, r.WeeklyReview.Conversations) || !reflect.DeepEqual(behaviors, r.WeeklyReview.Behaviors) {
		return invalid("周评必须保留五日已核任务、原文和本人行为，不能替换或静默遗漏")
	}
	return nil
}
