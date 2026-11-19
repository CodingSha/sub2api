package zhiyongli

import (
	"context"
	"time"
)

type ScheduleConfig struct {
	Version   int    `json:"version"`
	Enabled   bool   `json:"enabled"`
	FirstWeek string `json:"first_week"`
	FirstDay  string `json:"first_day,omitempty"`
	ActorID   int64  `json:"actor_id"`
}
type PreparationTarget struct {
	UserID    int64  `json:"user_id"`
	UserEmail string `json:"user_email"`
}
type DailyPreparation struct {
	ThroughDate string              `json:"through_date"`
	Scope       *WorkdayScope       `json:"workday_scope"`
	Users       []PreparationTarget `json:"users"`
}
type ScheduleResult struct {
	Enabled            bool              `json:"enabled"`
	Jobs               []Job             `json:"jobs"`
	NonEvaluationWeeks []string          `json:"non_evaluation_weeks"`
	Preparation        *DailyPreparation `json:"preparation,omitempty"`
	FirstDay           string            `json:"first_day,omitempty"`
}

// preparationDay 23点开始处理当日数据；凌晨及次日保留前一日补偿目标。
func preparationDay(now time.Time) time.Time {
	now = now.In(time.FixedZone("CST", 8*3600))
	day := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, now.Location())
	if now.Hour() < 23 {
		day = day.AddDate(0, 0, -1)
	}
	return day
}

// preparationThrough 封口后仍保留最后工作日补偿，不为周末新增空的本人评分任务。
func preparationThrough(scope *WorkdayScope, now time.Time) string {
	day := preparationDay(now).Format("2006-01-02")
	if day < scope.PeriodStart || day >= scope.PeriodEnd {
		return ""
	}
	through := ""
	for _, working := range scope.WorkingDates {
		if working <= day {
			through = working
		}
	}
	return through
}

// ScheduleDue 只入队已封口范围。配置由部署管理员提供，不接受Worker指定身份或评分。
func (s *Store) ScheduleDue(ctx context.Context, c ScheduleConfig, now time.Time) (ScheduleResult, error) {
	result := ScheduleResult{Enabled: c.Enabled, Jobs: []Job{}, NonEvaluationWeeks: []string{}}
	if !c.Enabled {
		return result, nil
	}
	tz := time.FixedZone("CST", 8*3600)
	now = now.In(tz)
	first, err := time.ParseInLocation("2006-01-02", c.FirstWeek, tz)
	if c.Version == 2 {
		first, err = time.ParseInLocation("2006-01-02", c.FirstDay, tz)
	}
	if c.Version != 1 && c.Version != 2 || err != nil || c.Version == 1 && (first.Weekday() != time.Monday || c.FirstDay != "") || c.Version == 2 && c.FirstWeek != "" || c.ActorID <= 0 || first.After(now) {
		return result, invalid("自动评审缺已确认启用周或管理身份")
	}
	var admin bool
	if err = s.db.QueryRowContext(ctx, "SELECT EXISTS(SELECT 1 FROM users WHERE id=$1 AND role='admin' AND deleted_at IS NULL)", c.ActorID).Scan(&admin); err != nil {
		return result, err
	}
	if !admin {
		return result, invalid("自动评审的管理身份无效")
	}
	users, err := s.personalUsers(ctx)
	if err != nil {
		return result, err
	}
	queue := func(kind, start string) error {
		jobs, e := s.startJobsForUsers(ctx, users, kind, start, c.ActorID, now)
		if e != nil {
			return e
		}
		result.Jobs = append(result.Jobs, jobs...)
		return nil
	}
	// 新日评计划独立版本化；补偿已封口日，不再同时排旧周评任务。
	if c.Version == 2 {
		result.FirstDay = c.FirstDay
		last := preparationDay(now)
		for cursor := first; !cursor.After(last); cursor = cursor.AddDate(0, 0, 1) {
			if e := queue("day", cursor.Format("2006-01-02")); e != nil {
				return result, e
			}
		}
		return result, nil
	}
	current := now.AddDate(0, 0, -(int(now.Weekday())+6)%7)
	current = time.Date(current.Year(), current.Month(), current.Day(), 0, 0, 0, 0, tz)
	for cursor := first; !cursor.After(current); cursor = cursor.AddDate(0, 0, 7) {
		scope, e := workdayWindow(cursor.Format("2006-01-02"), cursor.AddDate(0, 0, 7).Format("2006-01-02"), "")
		if e != nil {
			return result, e
		}
		freeze, _ := time.Parse(time.RFC3339, scope.FreezeAt)
		if through := preparationThrough(scope, now); through != "" {
			result.Preparation = &DailyPreparation{ThroughDate: through, Scope: scope, Users: users}
		}
		if freeze.After(now) {
			continue
		}
		if len(scope.WorkingDates) == 0 {
			result.NonEvaluationWeeks = append(result.NonEvaluationWeeks, cursor.Format("2006-01-02"))
			continue
		}
		if e = queue("week", cursor.Format("2006-01-02")); e != nil {
			return result, e
		}
	}
	for cursor := time.Date(first.Year(), first.Month(), 1, 0, 0, 0, 0, tz); cursor.Before(now); cursor = cursor.AddDate(0, 1, 0) {
		end := cursor.AddDate(0, 1, 0)
		if end.After(now) {
			break
		}
		scope, e := workdayWindow(cursor.Format("2006-01-02"), end.Format("2006-01-02"), "")
		if e != nil {
			return result, e
		}
		freeze, _ := time.Parse(time.RFC3339, scope.FreezeAt)
		if !freeze.After(now) && len(scope.WorkingDates) > 0 {
			if e = queue("month", cursor.Format("2006-01-02")); e != nil {
				return result, e
			}
		}
	}
	return result, nil
}
