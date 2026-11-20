package zhiyongli

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"time"
)

const activeDayRecheck = `j.user_id=$1 AND j.period_start=$2::date AND j.recheck_report_id IS NOT NULL AND j.state IN ('queued','running')`

func lockRecheckUser(ctx context.Context, tx *sql.Tx, userID int64) error {
	_, err := tx.ExecContext(ctx, `SELECT pg_advisory_xact_lock(249,hashint8($1::bigint))`, userID)
	return err
}

func (s *Store) StartDayRecheck(ctx context.Context, reportID, actor int64) (Job, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return Job{}, err
	}
	defer tx.Rollback()
	var userID int64
	var kind, start, end, status string
	err = tx.QueryRowContext(ctx, `SELECT user_id,period_type,period_start::text,period_end::text,publication_status FROM zhiyongli_reports WHERE id=$1`, reportID).Scan(&userID, &kind, &start, &end, &status)
	if errors.Is(err, sql.ErrNoRows) {
		return Job{}, ErrNotFound
	}
	if err != nil {
		return Job{}, err
	}
	if kind != "day" || (status != "draft" && status != "published") {
		return Job{}, invalid("只能复评当前已发布或待发布的日评分")
	}
	scope, err := readyWorkdayWindow(kind, start, time.Now())
	if err != nil {
		return Job{}, err
	}
	if scope.PeriodEnd != end {
		return Job{}, invalid("原报告日期与当前日评窗口不一致")
	}
	if err = personalUserAllowed(userID); err != nil {
		return Job{}, err
	}
	if err = lockRecheckUser(ctx, tx, userID); err != nil {
		return Job{}, err
	}
	var allowed bool
	err = tx.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM users u WHERE u.id=$1 AND u.deleted_at IS NULL AND NOT EXISTS(SELECT 1 FROM audit_whitelist_users w WHERE w.user_id=u.id))`, userID).Scan(&allowed)
	if err != nil {
		return Job{}, err
	}
	if !allowed {
		return Job{}, invalid("该账户不在当前日评范围内")
	}
	query := `SELECT ` + jobFields + ` FROM zhiyongli_jobs j JOIN users u ON u.id=j.user_id WHERE `
	job, err := scanJob(tx.QueryRowContext(ctx, query+activeDayRecheck, userID, start))
	if err == nil {
		return job, tx.Commit()
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return Job{}, err
	}
	raw, _ := json.Marshal(scope)
	var id int64
	err = tx.QueryRowContext(ctx, `WITH next_job AS (SELECT nextval(pg_get_serial_sequence('zhiyongli_jobs','id')) AS id)
		INSERT INTO zhiyongli_jobs(id,user_id,period_type,period_start,period_end,rules_version,created_by,scope_key,workday_scope,recheck_report_id,message)
		SELECT id,$1,'day',$2::date,$3::date,$4,$5,$6 || ':recheck:' || id::text,$7::jsonb,$8,'等待重新采集该日日评来源'
		FROM next_job RETURNING id`, userID, start, end, RulesVersion, actor, scope.Version+":"+scope.RegistrySHA, string(raw), reportID).Scan(&id)
	if err != nil {
		return Job{}, err
	}
	job, err = scanJob(tx.QueryRowContext(ctx, query+`j.id=$1`, id))
	if err != nil {
		return Job{}, err
	}
	return job, tx.Commit()
}
