package zhiyongli

import (
	"context"
	"fmt"
)

func (s *Store) companyRank(ctx context.Context, id, owner int64) (*CompanyRank, error) {
	excluded, err := excludedPersonalUsers()
	if err != nil {
		return nil, err
	}
	args := []any{id, owner}
	where := ""
	for _, userID := range excluded {
		args = append(args, userID)
		where += fmt.Sprintf(" AND r.user_id<>$%d", len(args))
	}
	// 仅比较同周、同规则版本的当前已发布结果；按展示分并列，0分也参与。
	query := `WITH mine AS (
		SELECT * FROM zhiyongli_reports WHERE id=$1 AND user_id=$2
		AND period_type='week' AND publication_status='published' AND body->>'score' IS NOT NULL
	), peers AS (
		SELECT (r.body->>'score')::numeric AS score FROM zhiyongli_reports r
		JOIN users u ON u.id=r.user_id CROSS JOIN mine m
		WHERE r.period_type=m.period_type AND r.period_start=m.period_start AND r.period_end=m.period_end
		AND r.publication_status='published' AND r.body->>'score' IS NOT NULL
		AND r.body->'policy_versions'=m.body->'policy_versions' AND u.deleted_at IS NULL
		AND NOT EXISTS(SELECT 1 FROM audit_whitelist_users w WHERE w.user_id=r.user_id)` + where + `
	) SELECT 1+count(*) FILTER(WHERE p.score>(m.body->>'score')::numeric),count(*),
		count(*) FILTER(WHERE p.score=(m.body->>'score')::numeric)>1 FROM peers p CROSS JOIN mine m`
	rank := &CompanyRank{}
	if err = s.db.QueryRowContext(ctx, query, args...).Scan(&rank.Position, &rank.Total, &rank.Tied); err != nil {
		return nil, err
	}
	if rank.Total == 0 {
		return nil, ErrNotFound
	}
	return rank, nil
}
