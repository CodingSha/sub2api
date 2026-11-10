package zhiyongli

import (
	"context"
	"time"
)

type ScheduleConfig struct {
	Version   int    `json:"version"`
	Enabled   bool   `json:"enabled"`
	FirstWeek string `json:"first_week"`
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
	if c.Version != 1 || err != nil || first.Weekday() != time.Monday || c.ActorID <= 0 || first.After(now) {
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
	current := now.AddDate(0, 0, -(int(now.Weekday())+6)%7)
	current = time.Date(current.Year(), current.Month(), current.Day(), 0, 0, 0, 0, tz)
	for cursor := first; !cursor.After(current); cursor = cursor.AddDate(0, 0, 7) {
		scope, e := workdayWindow(cursor.Format("2006-01-02"), cursor.AddDate(0, 0, 7).Format("2006-01-02"), "")
		if e != nil {
			return result, e
		}
		freeze, _ := time.Parse(time.RFC3339, scope.FreezeAt)
		if freeze.After(now) {
			// 每天02:00后维护前一日已采集材料；仅建立阅读导航，不提前评分。
			if cursor.Equal(current) && now.Hour() >= 2 {
				day := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, tz).AddDate(0, 0, -1)
				if !day.Before(cursor) {
					result.Preparation = &DailyPreparation{ThroughDate: day.Format("2006-01-02"), Scope: scope, Users: users}
				}
			}
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
