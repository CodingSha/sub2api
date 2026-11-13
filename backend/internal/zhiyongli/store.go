package zhiyongli

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
)

type Store struct{ db *sql.DB }

func NewStore(db *sql.DB) *Store { return &Store{db: db} }

func (s *Store) Import(ctx context.Context, b Bundle, actor int64) (Run, error) {
	if b.SchemaVersion != 1 || strings.TrimSpace(b.SourceName) == "" || len(b.SourceName) > 120 || len(b.Reports) == 0 || len(b.Reports) > 100 {
		return Run{}, invalid("结果包须为v1，包含来源名称和1至100份报告")
	}
	seen := map[string]bool{}
	for i := range b.Reports {
		if e := b.Reports[i].Validate(); e != nil {
			return Run{}, invalid(fmt.Sprintf("第%d份报告：%s", i+1, e))
		}
		r := b.Reports[i]
		key := fmt.Sprintf("%s/%s/%s", r.UserEmail, r.PeriodType, r.PeriodStart)
		if seen[key] {
			return Run{}, invalid("同一结果包不能包含同人同周期多份报告")
		}
		seen[key] = true
	}
	raw, e := json.Marshal(b)
	if e != nil {
		return Run{}, e
	}
	hash := sha256.Sum256(raw)
	digest := hex.EncodeToString(hash[:])
	tx, e := s.db.BeginTx(ctx, nil)
	if e != nil {
		return Run{}, e
	}
	defer tx.Rollback()
	// 导入与发布均为小事务；全模块事务锁避免同周期修订分配/发布交错。
	if _, e = tx.ExecContext(ctx, "SELECT pg_advisory_xact_lock(241241)"); e != nil {
		return Run{}, e
	}
	var run Run
	e = tx.QueryRowContext(ctx, `SELECT id,digest,source_name,report_count,created_at FROM zhiyongli_runs WHERE digest=$1`, digest).Scan(&run.ID, &run.Digest, &run.SourceName, &run.ReportCount, &run.CreatedAt)
	if e == nil {
		return run, tx.Commit()
	}
	if !errors.Is(e, sql.ErrNoRows) {
		return Run{}, e
	}
	e = tx.QueryRowContext(ctx, `INSERT INTO zhiyongli_runs(digest,source_name,report_count,created_by) VALUES($1,$2,$3,$4) RETURNING id,digest,source_name,report_count,created_at`, digest, b.SourceName, len(b.Reports), actor).Scan(&run.ID, &run.Digest, &run.SourceName, &run.ReportCount, &run.CreatedAt)
	if e != nil {
		return Run{}, e
	}
	for _, r := range b.Reports {
		var uid int64
		e = tx.QueryRowContext(ctx, `SELECT id FROM users WHERE email=$1 AND deleted_at IS NULL`, r.UserEmail).Scan(&uid)
		if errors.Is(e, sql.ErrNoRows) {
			return Run{}, invalid("结果账户不存在：" + r.UserEmail)
		}
		if e != nil {
			return Run{}, e
		}
		if e = personalUserAllowed(uid); e != nil {
			return Run{}, e
		}
		if _, e = tx.ExecContext(ctx, `UPDATE zhiyongli_reports SET publication_status='superseded' WHERE user_id=$1 AND period_type=$2 AND period_start=$3::date AND publication_status='draft'`, uid, r.PeriodType, r.PeriodStart); e != nil {
			return Run{}, e
		}
		// 证据只在专表保存，摘要/列表不返回原文。
		items := r.Items
		r.Items = append([]Item(nil), items...)
		for i := range r.Items {
			r.Items[i].Evidence = nil
		}
		body, e := json.Marshal(r)
		if e != nil {
			return Run{}, e
		}
		var id int64
		e = tx.QueryRowContext(ctx, `INSERT INTO zhiyongli_reports(run_id,user_id,period_type,period_start,period_end,revision,body)
            SELECT $1,$2,$3,$4::date,$5::date,COALESCE(MAX(revision),0)+1,$6::jsonb FROM zhiyongli_reports WHERE user_id=$2 AND period_type=$3 AND period_start=$4::date RETURNING id`, run.ID, uid, r.PeriodType, r.PeriodStart, r.PeriodEnd, string(body)).Scan(&id)
		if e != nil {
			return Run{}, e
		}
		for _, it := range items {
			for _, ev := range it.Evidence {
				body, e := json.Marshal(ev)
				if e != nil {
					return Run{}, e
				}
				if _, e = tx.ExecContext(ctx, `INSERT INTO zhiyongli_evidence(report_id,item_code,evidence_key,body) VALUES($1,$2,$3,$4::jsonb)`, id, it.Code, ev.ID, string(body)); e != nil {
					return Run{}, e
				}
			}
		}
	}
	if e = tx.Commit(); e != nil {
		return Run{}, e
	}
	return run, nil
}

type scanner interface{ Scan(...any) error }

func scanReport(row scanner) (Report, error) {
	var r Report
	var body []byte
	e := row.Scan(&r.ID, &r.UserID, &r.Revision, &r.PublicationStatus, &r.PublishedAt, &r.CreatedAt, &body)
	if errors.Is(e, sql.ErrNoRows) {
		return r, ErrNotFound
	}
	if e != nil {
		return r, e
	}
	e = json.Unmarshal(body, &r.Result)
	if e == nil {
		r.ScoreExplanation = scoreExplanation(r.Result)
	}
	return r, e
}

const reportColumns = "r.id,r.user_id,r.revision,r.publication_status,r.published_at,r.created_at,r.body"

func filterSQL(f Filter) (string, []any) {
	where := " WHERE true"
	args := []any{}
	add := func(expr string, v any) { args = append(args, v); where += fmt.Sprintf(expr, len(args)) }
	if f.UserID > 0 {
		add(" AND r.user_id=$%d", f.UserID)
		where += " AND r.publication_status='published'"
	}
	if f.PeriodType != "" {
		add(" AND r.period_type=$%d", f.PeriodType)
	}
	if f.PeriodStart != "" {
		add(" AND r.period_start=$%d::date", f.PeriodStart)
	}
	if f.Status != "" {
		add(" AND r.publication_status=$%d", f.Status)
	} else {
		where += " AND r.publication_status <> 'superseded'"
	}
	if f.Search != "" {
		add(" AND r.body->>'user_email' ILIKE $%d", "%"+f.Search+"%")
	}
	return where, args
}
func (s *Store) List(ctx context.Context, f Filter) ([]Report, int64, error) {
	where, args := filterSQL(f)
	var total int64
	if e := s.db.QueryRowContext(ctx, "SELECT count(*) FROM zhiyongli_reports r"+where, args...).Scan(&total); e != nil {
		return nil, 0, e
	}
	args = append(args, f.PageSize, (f.Page-1)*f.PageSize)
	query := "SELECT " + reportColumns + " FROM zhiyongli_reports r" + where + fmt.Sprintf(" ORDER BY r.period_start DESC,r.id DESC LIMIT $%d OFFSET $%d", len(args)-1, len(args))
	rows, e := s.db.QueryContext(ctx, query, args...)
	if e != nil {
		return nil, 0, e
	}
	defer rows.Close()
	items := []Report{}
	for rows.Next() {
		r, e := scanReport(rows)
		if e != nil {
			return nil, 0, e
		}
		r.Items = nil
		r.WeeklyReports = nil
		items = append(items, r)
	}
	return items, total, rows.Err()
}
func (s *Store) Get(ctx context.Context, id, owner int64) (Report, error) {
	where := " WHERE r.id=$1"
	args := []any{id}
	if owner > 0 {
		where += " AND r.user_id=$2 AND r.publication_status='published'"
		args = append(args, owner)
	}
	r, e := scanReport(s.db.QueryRowContext(ctx, "SELECT "+reportColumns+" FROM zhiyongli_reports r"+where, args...))
	if e != nil {
		return r, e
	}
	// 同一次查询复查权限：发布切换后旧修订不得继续从个人接口读取证据。
	query := `SELECT e.item_code,e.body FROM zhiyongli_evidence e JOIN zhiyongli_reports r ON r.id=e.report_id` + where + " ORDER BY e.item_code,e.evidence_key"
	rows, e := s.db.QueryContext(ctx, query, args...)
	if e != nil {
		return r, e
	}
	defer rows.Close()
	for i := range r.Items {
		r.Items[i].Evidence = []Evidence{}
	}
	for rows.Next() {
		var code string
		var raw []byte
		var ev Evidence
		if e = rows.Scan(&code, &raw); e != nil {
			return r, e
		}
		if e = json.Unmarshal(raw, &ev); e != nil {
			return r, e
		}
		for i := range r.Items {
			if r.Items[i].Code == code {
				r.Items[i].Evidence = append(r.Items[i].Evidence, ev)
			}
		}
	}
	if e = rows.Err(); e != nil {
		return r, e
	}
	// 周快照只用于后台重算月资格，不通过报告详情重复公开证据。
	r.WeeklyReports = nil
	return r, nil
}
func (s *Store) Publish(ctx context.Context, id int64, revision int, actor int64) (Report, error) {
	tx, e := s.db.BeginTx(ctx, nil)
	if e != nil {
		return Report{}, e
	}
	defer tx.Rollback()
	if _, e = tx.ExecContext(ctx, "SELECT pg_advisory_xact_lock(241241)"); e != nil {
		return Report{}, e
	}
	if e = publishReportTx(ctx, tx, id, revision, actor); e != nil {
		return Report{}, e
	}
	if e = tx.Commit(); e != nil {
		return Report{}, e
	}
	return s.Get(ctx, id, 0)
}

// 单份与整批发布复用相同的权限、修订及工作日范围检查。
func publishReportTx(ctx context.Context, tx *sql.Tx, id int64, revision int, actor int64) error {
	r, e := scanReport(tx.QueryRowContext(ctx, "SELECT "+reportColumns+" FROM zhiyongli_reports r WHERE r.id=$1 FOR UPDATE", id))
	if e != nil {
		return e
	}
	if revision != r.Revision || r.PublicationStatus == "superseded" {
		return ErrConflict
	}
	if e = personalUserAllowed(r.UserID); e != nil {
		return e
	}
	if r.PublicationStatus == "published" {
		return nil
	}
	if r.PolicyVersions.Rules == RulesVersion && r.WorkdayScope == nil {
		return invalid("该候选为历史自然周范围，请按当前工作日范围重评后发布")
	}
	var latest int
	if e = tx.QueryRowContext(ctx, `SELECT MAX(revision) FROM zhiyongli_reports WHERE user_id=$1 AND period_type=$2 AND period_start=$3::date`, r.UserID, r.PeriodType, r.PeriodStart).Scan(&latest); e != nil {
		return e
	}
	if latest != revision {
		return ErrConflict
	}
	if _, e = tx.ExecContext(ctx, `UPDATE zhiyongli_reports SET publication_status='superseded' WHERE user_id=$1 AND period_type=$2 AND period_start=$3::date AND publication_status='published'`, r.UserID, r.PeriodType, r.PeriodStart); e != nil {
		return e
	}
	if _, e = tx.ExecContext(ctx, `UPDATE zhiyongli_reports SET publication_status='published',published_by=$2,published_at=now() WHERE id=$1`, id, actor); e != nil {
		return e
	}
	return nil
}
func (s *Store) Runs(ctx context.Context, page, size int) ([]Run, int64, error) {
	var total int64
	if e := s.db.QueryRowContext(ctx, `SELECT count(*) FROM zhiyongli_runs`).Scan(&total); e != nil {
		return nil, 0, e
	}
	rows, e := s.db.QueryContext(ctx, `SELECT id,digest,source_name,report_count,created_at FROM zhiyongli_runs ORDER BY id DESC LIMIT $1 OFFSET $2`, size, (page-1)*size)
	if e != nil {
		return nil, 0, e
	}
	defer rows.Close()
	items := []Run{}
	for rows.Next() {
		var r Run
		if e = rows.Scan(&r.ID, &r.Digest, &r.SourceName, &r.ReportCount, &r.CreatedAt); e != nil {
			return nil, 0, e
		}
		items = append(items, r)
	}
	return items, total, rows.Err()
}
