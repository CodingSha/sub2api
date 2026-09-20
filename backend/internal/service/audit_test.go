package service

import (
	"context"
	"database/sql"
	"testing"

	"github.com/Wei-Shaw/sub2api/internal/pkg/pagination"
	"github.com/stretchr/testify/require"
)

type auditServiceRepoStub struct {
	ids       map[int64]bool
	loadCalls int
}

func (r *auditServiceRepoStub) Create(context.Context, *LLMAuditLog) error { return nil }
func (r *auditServiceRepoStub) List(context.Context, LLMAuditLogFilter) ([]LLMAuditLog, *pagination.PaginationResult, error) {
	return nil, nil, nil
}
func (r *auditServiceRepoStub) ListWhitelist(context.Context) ([]AuditWhitelistEntry, error) {
	return nil, nil
}
func (r *auditServiceRepoStub) ListWhitelistUserIDs(context.Context) ([]int64, error) {
	r.loadCalls++
	ids := make([]int64, 0, len(r.ids))
	for id, enabled := range r.ids {
		if enabled {
			ids = append(ids, id)
		}
	}
	return ids, nil
}
func (r *auditServiceRepoStub) AddWhitelist(_ context.Context, userID int64, _ *int64) (*AuditWhitelistEntry, error) {
	if userID == 404 {
		return nil, sql.ErrNoRows
	}
	if r.ids == nil {
		r.ids = make(map[int64]bool)
	}
	r.ids[userID] = true
	return &AuditWhitelistEntry{UserID: userID}, nil
}
func (r *auditServiceRepoStub) RemoveWhitelist(_ context.Context, userID int64) error {
	delete(r.ids, userID)
	return nil
}

func TestAuditServiceWhitelistUpdatesHotPathCache(t *testing.T) {
	repo := &auditServiceRepoStub{ids: map[int64]bool{7: true}}
	svc := NewAuditService(repo)

	require.True(t, svc.IsAuditWhitelisted(context.Background(), 7))
	require.False(t, svc.IsAuditWhitelisted(context.Background(), 8))
	require.Equal(t, 1, repo.loadCalls)

	_, err := svc.AddWhitelist(context.Background(), 8, nil)
	require.NoError(t, err)
	require.True(t, svc.IsAuditWhitelisted(context.Background(), 8))
	require.Equal(t, 1, repo.loadCalls)

	require.NoError(t, svc.RemoveWhitelist(context.Background(), 7))
	require.False(t, svc.IsAuditWhitelisted(context.Background(), 7))
	require.Equal(t, 1, repo.loadCalls)
}

func TestAuditServiceWhitelistRejectsUnknownUser(t *testing.T) {
	svc := NewAuditService(&auditServiceRepoStub{})
	_, err := svc.AddWhitelist(context.Background(), 404, nil)
	require.ErrorIs(t, err, ErrUserNotFound)
}
