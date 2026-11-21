//go:build unit

package middleware

import (
	"context"
	"database/sql"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/Wei-Shaw/sub2api/internal/pkg/ctxkey"
	"github.com/Wei-Shaw/sub2api/internal/repository"
	"github.com/Wei-Shaw/sub2api/internal/service"
	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	_ "github.com/lib/pq"
	"github.com/stretchr/testify/require"
)

// 使用显式本地专用库，验证真实事务和唯一约束，不触碰生产数据。
func TestClientRequestIDSeparateCallsAndBillingRetryWithPostgres(t *testing.T) {
	dsn := os.Getenv("USAGE_BILLING_TEST_DSN")
	if dsn == "" {
		t.Skip("需要显式本地专用 PostgreSQL 测试库")
	}
	db, err := sql.Open("postgres", dsn)
	require.NoError(t, err)
	defer db.Close()
	db.SetMaxOpenConns(1)
	schema := "billing_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	_, err = db.Exec("CREATE SCHEMA " + schema)
	require.NoError(t, err)
	defer db.Exec("DROP SCHEMA " + schema + " CASCADE")
	_, err = db.Exec("SET search_path TO " + schema)
	require.NoError(t, err)
	_, err = db.Exec(`
		CREATE TABLE usage_billing_dedup (id BIGSERIAL PRIMARY KEY, request_id TEXT, api_key_id BIGINT,
			request_fingerprint TEXT, UNIQUE(request_id,api_key_id));
		CREATE TABLE usage_billing_dedup_archive (request_id TEXT, api_key_id BIGINT, request_fingerprint TEXT);
		CREATE TABLE users (id BIGINT PRIMARY KEY, balance NUMERIC, updated_at TIMESTAMPTZ, deleted_at TIMESTAMPTZ);
		INSERT INTO users(id,balance) VALUES(1,100)`)
	require.NoError(t, err)
	repo := repository.NewUsageBillingRepository(nil, db)
	var commands []*service.UsageBillingCommand
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.Use(RequestLogger(), ClientRequestID())
	router.POST("/responses", func(c *gin.Context) {
		id := c.Request.Context().Value(ctxkey.UsageBillingRequestID).(string)
		cmd := &service.UsageBillingCommand{
			RequestID: "request:" + id, APIKeyID: 2, UserID: 1, AccountID: 3,
			BalanceCost: float64(len(commands) + 1), RequestPayloadHash: c.GetHeader("Test-Payload-Hash"),
		}
		result, err := repo.Apply(c.Request.Context(), cmd)
		require.NoError(t, err)
		require.True(t, result.Applied)
		commands = append(commands, cmd)
		c.Status(http.StatusOK)
	})
	for _, payload := range []string{"first-codex-call", "second-codex-call"} {
		req := httptest.NewRequest(http.MethodPost, "/responses", nil)
		req.Header.Set(clientRequestIDHeader, "same-codex-conversation")
		req.Header.Set(requestIDHeader, "also-reused-correlation")
		req.Header.Set("Test-Payload-Hash", payload)
		response := httptest.NewRecorder()
		router.ServeHTTP(response, req)
		require.Equal(t, http.StatusOK, response.Code)
		require.Equal(t, "same-codex-conversation", response.Header().Get(clientRequestIDHeader))
	}
	require.NotEqual(t, commands[0].RequestID, commands[1].RequestID)
	for _, cmd := range commands {
		result, err := repo.Apply(context.Background(), cmd)
		require.NoError(t, err)
		require.False(t, result.Applied)
	}
	conflict := *commands[0]
	conflict.RequestPayloadHash = "different-payload-same-call"
	conflict.RequestFingerprint = ""
	_, err = repo.Apply(context.Background(), &conflict)
	require.ErrorIs(t, err, service.ErrUsageBillingRequestConflict)
	var count int
	var balance float64
	require.NoError(t, db.QueryRow("SELECT count(*) FROM usage_billing_dedup").Scan(&count))
	require.NoError(t, db.QueryRow("SELECT balance FROM users WHERE id=1").Scan(&balance))
	require.Equal(t, 2, count)
	require.InDelta(t, 97, balance, 1e-9)
}
