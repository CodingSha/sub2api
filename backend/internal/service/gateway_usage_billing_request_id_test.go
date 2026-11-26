//go:build unit

package service

import (
	"context"
	"strings"
	"testing"

	"github.com/Wei-Shaw/sub2api/internal/pkg/ctxkey"
	"github.com/stretchr/testify/require"
)

func TestResolveUsageBillingRequestID_ForcedWebSearchBeatsClientID(t *testing.T) {
	t.Parallel()
	ctx := context.WithValue(context.Background(), ctxkey.ClientRequestID, "client-shared-id")
	ctx = context.WithValue(ctx, ctxkey.UsageBillingRequestID, "server-call-1")
	got := resolveUsageBillingRequestID(ctx, "web_search:uuid-1")
	require.Equal(t, "web_search:uuid-1", got)
}

func TestResolveUsageBillingRequestID_ServerCallWinsOverPlainUpstream(t *testing.T) {
	t.Parallel()
	ctx := context.WithValue(context.Background(), ctxkey.ClientRequestID, "client-shared-id")
	ctx = context.WithValue(ctx, ctxkey.UsageBillingRequestID, "server-call-1")
	got := resolveUsageBillingRequestID(ctx, "resp_abc")
	require.Equal(t, "request:server-call-1", got)
}

func TestResolveUsageBillingRequestID_CorrelationCannotBecomeBillingIdentity(t *testing.T) {
	ctx := context.WithValue(context.Background(), ctxkey.ClientRequestID, "reused-codex-id")
	require.Equal(t, "resp_first", resolveUsageBillingRequestID(ctx, "resp_first"))
	require.Equal(t, "resp_second", resolveUsageBillingRequestID(ctx, "resp_second"))
	first := context.WithValue(ctx, ctxkey.UsageBillingRequestID, "server-first")
	second := context.WithValue(ctx, ctxkey.UsageBillingRequestID, "server-second")
	require.Equal(t, "request:server-first", resolveUsageBillingRequestID(first, "resp_first"))
	// 同一调用切换上游或重试落账仍保留原键，不同 HTTP 调用必须有不同键。
	require.Equal(t, resolveUsageBillingRequestID(first, "resp_first"), resolveUsageBillingRequestID(first, "resp_retry"))
	require.NotEqual(t, resolveUsageBillingRequestID(first, "resp_first"), resolveUsageBillingRequestID(second, "resp_second"))
}

func TestIsForcedUsageBillingRequestID(t *testing.T) {
	t.Parallel()
	require.True(t, isForcedUsageBillingRequestID("web_search:x"))
	require.True(t, isForcedUsageBillingRequestID("grok-video:task-1"))
	require.True(t, isForcedUsageBillingRequestID("grok_audio:up-1"))
	require.True(t, isForcedUsageBillingRequestID("grok_realtime:sess-1"))
	require.True(t, isForcedUsageBillingRequestID(GeminiCachedContentCreateUsageRequestID("abc")))
	require.False(t, isForcedUsageBillingRequestID("resp_abc"))
}

func TestGeminiCachedContentUsageRequestIDs(t *testing.T) {
	t.Parallel()
	publicID, err := NewGeminiCachedContentPublicID()
	require.NoError(t, err)
	create := GeminiCachedContentCreateUsageRequestID(publicID)
	patch := GeminiCachedContentPatchUsageRequestID(publicID)
	require.Equal(t, "gcache:create:"+publicID, create)
	require.True(t, strings.HasPrefix(patch, "gcache:patch:"+publicID+":"))
	require.NotEqual(t, patch, GeminiCachedContentPatchUsageRequestID(publicID), "每次延长各自唯一")
	require.LessOrEqual(t, len(create), 64, "usage_logs.request_id 列长 64")
	require.LessOrEqual(t, len(patch), 64, "usage_logs.request_id 列长 64")

	ctx := context.WithValue(context.Background(), ctxkey.ClientRequestID, "client-shared-id")
	require.Equal(t, create, resolveUsageBillingRequestID(ctx, create), "缓存费用行不被客户端请求 id 覆盖")
	require.Equal(t, patch, resolveUsageBillingRequestID(ctx, patch))
}

func TestStableGrokAudioBillingRequestID(t *testing.T) {
	t.Parallel()
	require.Equal(t, "grok_audio:up-1", StableGrokAudioBillingRequestID("up-1"))
	require.Equal(t, "grok_audio:up-1", StableGrokAudioBillingRequestID("grok_audio:up-1"))
	got := StableGrokAudioBillingRequestID("")
	require.True(t, strings.HasPrefix(got, "grok_audio:"))
	require.Greater(t, len(got), len("grok_audio:"))
}

func TestStableGrokRealtimeBillingRequestID(t *testing.T) {
	t.Parallel()
	require.Equal(t, "grok_realtime:s1", StableGrokRealtimeBillingRequestID("s1"))
	require.Equal(t, "grok_realtime:s1", StableGrokRealtimeBillingRequestID("grok_realtime:s1"))
	got := StableGrokRealtimeBillingRequestID("")
	require.True(t, strings.HasPrefix(got, "grok_realtime:"))
}

func TestResolveUsageBillingRequestID_ForcedGrokAudioBeatsClientID(t *testing.T) {
	t.Parallel()
	ctx := context.WithValue(context.Background(), ctxkey.ClientRequestID, "client-shared-id")
	ctx = context.WithValue(ctx, ctxkey.UsageBillingRequestID, "server-call-1")
	got := resolveUsageBillingRequestID(ctx, StableGrokAudioBillingRequestID("up-9"))
	require.Equal(t, "grok_audio:up-9", got)
}
