package zhiyongli

import (
	"context"
	"fmt"
	"time"
)

type PublishReference struct {
	ID       int64 `json:"id"`
	Revision int   `json:"revision"`
}

type BatchPublishInput struct {
	PeriodType  string             `json:"period_type"`
	PeriodStart string             `json:"period_start"`
	Reports     []PublishReference `json:"reports"`
}

type BatchPublishResult struct {
	PublishedCount int     `json:"published_count"`
	ReportIDs      []int64 `json:"report_ids"`
}

func (s *Store) BatchPublish(ctx context.Context, input BatchPublishInput, actor int64) (BatchPublishResult, error) {
	result := BatchPublishResult{ReportIDs: []int64{}}
	start, err := time.Parse("2006-01-02", input.PeriodStart)
	if err != nil || start.Format("2006-01-02") != input.PeriodStart || (input.PeriodType != "week" && input.PeriodType != "month") || len(input.Reports) < 1 || len(input.Reports) > 100 {
		return result, invalid("请选择同一周期的1至100份报告")
	}
	seen := map[int64]bool{}
	for _, ref := range input.Reports {
		if ref.ID <= 0 || ref.Revision < 1 || seen[ref.ID] {
			return result, invalid("报告ID和修订须有效且不能重复")
		}
		seen[ref.ID] = true
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return result, err
	}
	defer tx.Rollback()
	if _, err = tx.ExecContext(ctx, "SELECT pg_advisory_xact_lock(241241)"); err != nil {
		return result, err
	}
	// 显式ID与修订冻结本次确认范围；任一不符整批回滚，不出现未说明的部分发布。
	for _, ref := range input.Reports {
		r, err := scanReport(tx.QueryRowContext(ctx, "SELECT "+reportColumns+" FROM zhiyongli_reports r WHERE r.id=$1 FOR UPDATE", ref.ID))
		if err != nil {
			return result, err
		}
		if r.PeriodType != input.PeriodType || r.PeriodStart != input.PeriodStart {
			return result, invalid("批量发布不能包含其他周期的报告")
		}
		if err = publishReportTx(ctx, tx, ref.ID, ref.Revision, actor); err != nil {
			return result, fmt.Errorf("报告%d未通过检查，整批未发布：%w", ref.ID, err)
		}
	}
	if err = tx.Commit(); err != nil {
		return result, err
	}
	for _, ref := range input.Reports {
		result.ReportIDs = append(result.ReportIDs, ref.ID)
	}
	result.PublishedCount = len(result.ReportIDs)
	return result, nil
}
