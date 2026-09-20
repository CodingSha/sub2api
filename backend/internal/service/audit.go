package service

import (
	"context"
	"database/sql"
	"errors"
	"sync"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/pkg/pagination"
)

const (
	AuditCaptureMaxBytes = 64 * 1024
)

type LLMAuditLog struct {
	ID                int64     `json:"id"`
	RequestID         string    `json:"request_id"`
	SessionID         string    `json:"session_id"`
	RequestCount      int       `json:"request_count"`
	UserID            *int64    `json:"user_id,omitempty"`
	UserEmail         string    `json:"user_email"`
	APIKeyID          *int64    `json:"api_key_id,omitempty"`
	APIKeyName        string    `json:"api_key_name"`
	GroupID           *int64    `json:"group_id,omitempty"`
	GroupName         string    `json:"group_name"`
	Platform          string    `json:"platform"`
	Endpoint          string    `json:"endpoint"`
	Method            string    `json:"method"`
	Model             string    `json:"model"`
	StatusCode        int       `json:"status_code"`
	RequestBody       string    `json:"request_body"`
	ResponseBody      string    `json:"response_body"`
	RequestTruncated  bool      `json:"request_truncated"`
	ResponseTruncated bool      `json:"response_truncated"`
	DurationMS        int       `json:"duration_ms"`
	IPAddress         string    `json:"ip_address"`
	UserAgent         string    `json:"user_agent"`
	CreatedAt         time.Time `json:"created_at"`
	UpdatedAt         time.Time `json:"updated_at"`
}

type LLMAuditLogFilter struct {
	Pagination pagination.PaginationParams
	Search     string
	Platform   string
	Model      string
	Endpoint   string
	From       *time.Time
	To         *time.Time
}

type AuditWhitelistEntry struct {
	UserID         int64     `json:"user_id"`
	Email          string    `json:"email"`
	Username       string    `json:"username"`
	CreatedBy      *int64    `json:"created_by,omitempty"`
	CreatedByEmail string    `json:"created_by_email,omitempty"`
	CreatedAt      time.Time `json:"created_at"`
}

type AuditRepository interface {
	Create(ctx context.Context, log *LLMAuditLog) error
	List(ctx context.Context, filter LLMAuditLogFilter) ([]LLMAuditLog, *pagination.PaginationResult, error)
	ListWhitelist(ctx context.Context) ([]AuditWhitelistEntry, error)
	ListWhitelistUserIDs(ctx context.Context) ([]int64, error)
	AddWhitelist(ctx context.Context, userID int64, createdBy *int64) (*AuditWhitelistEntry, error)
	RemoveWhitelist(ctx context.Context, userID int64) error
}

type AuditService struct {
	repo AuditRepository

	whitelistMu        sync.RWMutex
	whitelistUserIDs   map[int64]struct{}
	whitelistExpiresAt time.Time
}

const auditWhitelistCacheTTL = 15 * time.Second

func NewAuditService(repo AuditRepository) *AuditService {
	return &AuditService{repo: repo}
}

func (s *AuditService) Create(ctx context.Context, log *LLMAuditLog) error {
	if s == nil || s.repo == nil || log == nil {
		return nil
	}
	return s.repo.Create(ctx, log)
}

func (s *AuditService) List(ctx context.Context, filter LLMAuditLogFilter) ([]LLMAuditLog, *pagination.PaginationResult, error) {
	if filter.Pagination.Page <= 0 {
		filter.Pagination.Page = 1
	}
	if filter.Pagination.PageSize <= 0 {
		filter.Pagination.PageSize = 20
	}
	if filter.Pagination.PageSize > 100 {
		filter.Pagination.PageSize = 100
	}
	return s.repo.List(ctx, filter)
}

func (s *AuditService) ListWhitelist(ctx context.Context) ([]AuditWhitelistEntry, error) {
	if s == nil || s.repo == nil {
		return []AuditWhitelistEntry{}, nil
	}
	return s.repo.ListWhitelist(ctx)
}

func (s *AuditService) AddWhitelist(ctx context.Context, userID int64, createdBy *int64) (*AuditWhitelistEntry, error) {
	if s == nil || s.repo == nil {
		return nil, errors.New("audit service is unavailable")
	}
	entry, err := s.repo.AddWhitelist(ctx, userID, createdBy)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrUserNotFound
	}
	if err != nil {
		return nil, err
	}
	s.whitelistMu.Lock()
	if s.whitelistUserIDs != nil && time.Now().Before(s.whitelistExpiresAt) {
		s.whitelistUserIDs[userID] = struct{}{}
	} else {
		s.whitelistExpiresAt = time.Time{}
	}
	s.whitelistMu.Unlock()
	return entry, nil
}

func (s *AuditService) RemoveWhitelist(ctx context.Context, userID int64) error {
	if s == nil || s.repo == nil {
		return errors.New("audit service is unavailable")
	}
	if err := s.repo.RemoveWhitelist(ctx, userID); err != nil {
		return err
	}
	s.whitelistMu.Lock()
	delete(s.whitelistUserIDs, userID)
	s.whitelistMu.Unlock()
	return nil
}

// IsAuditWhitelisted is used on the gateway hot path. The short-lived snapshot
// keeps normal requests away from PostgreSQL while still observing changes made
// by another application instance within a bounded interval.
func (s *AuditService) IsAuditWhitelisted(ctx context.Context, userID int64) bool {
	if s == nil || s.repo == nil || userID <= 0 {
		return false
	}
	now := time.Now()
	s.whitelistMu.RLock()
	if s.whitelistUserIDs != nil && now.Before(s.whitelistExpiresAt) {
		_, ok := s.whitelistUserIDs[userID]
		s.whitelistMu.RUnlock()
		return ok
	}
	s.whitelistMu.RUnlock()

	s.whitelistMu.Lock()
	defer s.whitelistMu.Unlock()
	if s.whitelistUserIDs != nil && now.Before(s.whitelistExpiresAt) {
		_, ok := s.whitelistUserIDs[userID]
		return ok
	}
	ids, err := s.repo.ListWhitelistUserIDs(ctx)
	if err != nil {
		// Keep the last successful snapshot and retry soon. A startup failure
		// defaults to auditing so a database outage cannot disable safeguards.
		s.whitelistExpiresAt = now.Add(time.Second)
		_, ok := s.whitelistUserIDs[userID]
		return ok
	}
	s.whitelistUserIDs = make(map[int64]struct{}, len(ids))
	for _, id := range ids {
		s.whitelistUserIDs[id] = struct{}{}
	}
	s.whitelistExpiresAt = now.Add(auditWhitelistCacheTTL)
	_, ok := s.whitelistUserIDs[userID]
	return ok
}
