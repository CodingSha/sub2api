package zhiyongli

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"reflect"
	"time"
)

// Job 不返回缓存路径、凭证、原始请求或模型响应。
type Job struct {
	ID             int64         `json:"id"`
	UserID         int64         `json:"user_id"`
	UserEmail      string        `json:"user_email"`
	PeriodType     string        `json:"period_type"`
	PeriodStart    string        `json:"period_start"`
	PeriodEnd      string        `json:"period_end"`
	RulesVersion   string        `json:"rules_version"`
	ScopeKey       string        `json:"scope_key"`
	WorkdayScope   *WorkdayScope `json:"workday_scope,omitempty"`
	State          string        `json:"state"`
	Phase          string        `json:"phase"`
	CompletedUnits int           `json:"completed_units"`
	TotalUnits     int           `json:"total_units"`
	Message        string        `json:"message"`
	CreatedAt      time.Time     `json:"created_at"`
	UpdatedAt      time.Time     `json:"updated_at"`
}

type StartJobs struct {
	UserIDs     []int64 `json:"user_ids"`
	PeriodType  string  `json:"period_type"`
	PeriodStart string  `json:"period_start"`
}

func jobWindow(kind, start string, now time.Time) (string, error) {
	scope, err := readyWorkdayWindow(kind, start, now)
	if err != nil {
		return "", err
	}
	return scope.PeriodEnd, nil
}

func readyWorkdayWindow(kind, start string, now time.Time) (*WorkdayScope, error) {
	a, err := time.ParseInLocation("2006-01-02", start, time.FixedZone("CST", 8*3600))
	if err != nil || a.Format("2006-01-02") != start {
		return nil, invalid("周期开始日期无效")
	}
	var b time.Time
	switch kind {
	case "week":
		if a.Weekday() != time.Monday {
			return nil, invalid("周评分必须从周一开始")
		}
		b = a.AddDate(0, 0, 7)
	case "month":
		if a.Day() != 1 {
			return nil, invalid("月评分必须从每月1日开始")
		}
		b = a.AddDate(0, 1, 0)
	default:
		return nil, invalid("周期类型必须为week或month")
	}
	scope, err := workdayWindow(start, b.Format("2006-01-02"), "")
	if err != nil {
		return nil, err
	}
	if len(scope.WorkingDates) == 0 {
		return nil, invalid("该期间没有工作日，无需生成员工评分")
	}
	freeze, _ := time.Parse(time.RFC3339, scope.FreezeAt)
	if freeze.After(now) {
		return nil, invalid("工作日期间来源尚未封口")
	}
	return scope, nil
}

const jobFields = `j.id,j.user_id,u.email,j.period_type,j.period_start::text,j.period_end::text,j.rules_version,j.state,j.phase,j.completed_units,j.total_units,j.message,j.created_at,j.updated_at,j.scope_key,j.workday_scope`

func scanJob(row interface{ Scan(...any) error }) (Job, error) {
	var j Job
	var raw []byte
	err := row.Scan(&j.ID, &j.UserID, &j.UserEmail, &j.PeriodType, &j.PeriodStart, &j.PeriodEnd, &j.RulesVersion, &j.State, &j.Phase, &j.CompletedUnits, &j.TotalUnits, &j.Message, &j.CreatedAt, &j.UpdatedAt, &j.ScopeKey, &raw)
	if err == nil && len(raw) > 0 {
		err = json.Unmarshal(raw, &j.WorkdayScope)
	}
	return j, err
}

func (s *Store) StartJobs(ctx context.Context, input StartJobs, actor int64) ([]Job, error) {
	return s.startJobsAt(ctx, input, actor, time.Now())
}

func (s *Store) startJobsAt(ctx context.Context, input StartJobs, actor int64, now time.Time) ([]Job, error) {
	scope, err := readyWorkdayWindow(input.PeriodType, input.PeriodStart, now)
	if err != nil {
		return nil, err
	}
	if len(input.UserIDs) < 1 || len(input.UserIDs) > 20 {
		return nil, invalid("请选择1至20个账户")
	}
	seen := map[int64]bool{}
	for _, id := range input.UserIDs {
		if id <= 0 || seen[id] {
			return nil, invalid("账户ID无效或重复")
		}
		seen[id] = true
		if err = personalUserAllowed(id); err != nil {
			return nil, err
		}
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	scopeKey := scope.Version + ":" + scope.RegistrySHA
	raw, _ := json.Marshal(scope)
	result := make([]Job, 0, len(input.UserIDs))
	for _, id := range input.UserIDs {
		var allowed bool
		err = tx.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM users u WHERE u.id=$1 AND u.deleted_at IS NULL AND NOT EXISTS(SELECT 1 FROM audit_whitelist_users w WHERE w.user_id=u.id))`, id).Scan(&allowed)
		if err != nil {
			return nil, err
		}
		if !allowed {
			return nil, invalid("所选账户不存在或已排除审计")
		}
		_, err = tx.ExecContext(ctx, `INSERT INTO zhiyongli_jobs(user_id,period_type,period_start,period_end,rules_version,created_by,scope_key,workday_scope) VALUES($1,$2,$3::date,$4::date,$5,$6,$7,$8::jsonb) ON CONFLICT(user_id,period_type,period_start,rules_version,scope_key) DO NOTHING`, id, input.PeriodType, input.PeriodStart, scope.PeriodEnd, RulesVersion, actor, scopeKey, string(raw))
		if err != nil {
			return nil, err
		}
		j, e := scanJob(tx.QueryRowContext(ctx, `SELECT `+jobFields+` FROM zhiyongli_jobs j JOIN users u ON u.id=j.user_id WHERE j.user_id=$1 AND j.period_type=$2 AND j.period_start=$3::date AND j.rules_version=$4 AND j.scope_key=$5`, id, input.PeriodType, input.PeriodStart, RulesVersion, scopeKey))
		if e != nil {
			return nil, e
		}
		result = append(result, j)
	}
	return result, tx.Commit()
}

func (s *Store) Jobs(ctx context.Context, page, size int) ([]Job, int64, error) {
	var total int64
	if err := s.db.QueryRowContext(ctx, `SELECT count(*) FROM zhiyongli_jobs`).Scan(&total); err != nil {
		return nil, 0, err
	}
	rows, err := s.db.QueryContext(ctx, `SELECT `+jobFields+` FROM zhiyongli_jobs j JOIN users u ON u.id=j.user_id ORDER BY j.id DESC LIMIT $1 OFFSET $2`, size, (page-1)*size)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()
	items := []Job{}
	for rows.Next() {
		j, e := scanJob(rows)
		if e != nil {
			return nil, 0, e
		}
		items = append(items, j)
	}
	return items, total, rows.Err()
}

func (s *Store) ResumeJob(ctx context.Context, id int64) (Job, error) {
	// 与Worker使用同一会话锁空间；执行中不覆盖状态或重置回执。
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return Job{}, err
	}
	defer tx.Rollback()
	var locked bool
	if err = tx.QueryRowContext(ctx, `SELECT pg_try_advisory_xact_lock(245,$1::int)`, id).Scan(&locked); err != nil {
		return Job{}, err
	}
	if !locked {
		return Job{}, ErrConflict
	}
	j, err := scanJob(tx.QueryRowContext(ctx, `SELECT `+jobFields+` FROM zhiyongli_jobs j JOIN users u ON u.id=j.user_id WHERE j.id=$1 FOR UPDATE OF j`, id))
	if errors.Is(err, sql.ErrNoRows) {
		return Job{}, ErrNotFound
	}
	if err != nil {
		return Job{}, err
	}
	if err = personalUserAllowed(j.UserID); err != nil {
		return Job{}, err
	}
	if j.RulesVersion != RulesVersion || (j.State != "failed" && j.State != "needs_review") {
		return Job{}, ErrConflict
	}
	_, err = tx.ExecContext(ctx, `UPDATE zhiyongli_jobs SET state='queued',message='等待从已有回执继续校验；不重新发送已发请求',updated_at=now() WHERE id=$1`, id)
	if err != nil {
		return Job{}, err
	}
	j.State = "queued"
	j.Message = "等待从已有回执继续校验；不重新发送已发请求"
	j.UpdatedAt = time.Now()
	return j, tx.Commit()
}

// ImportJob 限定Worker只能为当前任务接入候选，不能发布或写入别人的周期。
func (s *Store) ImportJob(ctx context.Context, id int64, bundle Bundle) (Run, error) {
	j, err := scanJob(s.db.QueryRowContext(ctx, `SELECT `+jobFields+` FROM zhiyongli_jobs j JOIN users u ON u.id=j.user_id WHERE j.id=$1`, id))
	if errors.Is(err, sql.ErrNoRows) {
		return Run{}, ErrNotFound
	}
	if err != nil {
		return Run{}, err
	}
	if j.State != "running" || j.RulesVersion != RulesVersion || len(bundle.Reports) != 1 {
		return Run{}, ErrConflict
	}
	r := bundle.Reports[0]
	if r.UserEmail != j.UserEmail || r.PeriodType != j.PeriodType || r.PeriodStart != j.PeriodStart || r.PeriodEnd != j.PeriodEnd || r.PolicyVersions.Rules != j.RulesVersion || !reflect.DeepEqual(r.WorkdayScope, j.WorkdayScope) {
		return Run{}, invalid("候选报告与评审任务人员、周期或规则不一致")
	}
	// Import本身按包摘要幂等；回执写入前崩溃时仍可续用同一报告，不创建重复修订。
	var actor int64
	if err = s.db.QueryRowContext(ctx, `SELECT created_by FROM zhiyongli_jobs WHERE id=$1`, j.ID).Scan(&actor); err != nil {
		return Run{}, err
	}
	return s.Import(ctx, bundle, actor)
}
