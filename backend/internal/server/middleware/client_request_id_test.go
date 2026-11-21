package middleware

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Wei-Shaw/sub2api/internal/pkg/ctxkey"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestClientRequestIDGeneratesAndExposesID(t *testing.T) {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.Use(ClientRequestID())
	router.GET("/", func(c *gin.Context) {
		value, _ := c.Request.Context().Value(ctxkey.ClientRequestID).(string)
		c.String(http.StatusOK, value)
	})

	req := httptest.NewRequest(http.MethodGet, "/", nil)
	w := httptest.NewRecorder()

	router.ServeHTTP(w, req)

	require.Equal(t, http.StatusOK, w.Code)
	require.NotEmpty(t, w.Body.String())
	require.Equal(t, w.Body.String(), w.Header().Get(clientRequestIDHeader))
}

func TestClientRequestIDBoundsExistingContextID(t *testing.T) {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.Use(ClientRequestID())
	router.GET("/", func(c *gin.Context) {
		value, _ := c.Request.Context().Value(ctxkey.ClientRequestID).(string)
		c.String(http.StatusOK, value)
	})
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req = req.WithContext(context.WithValue(req.Context(), ctxkey.ClientRequestID, strings.Repeat("x", 200)))
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)
	require.Len(t, w.Body.String(), 36)
	require.NotEqual(t, strings.Repeat("x", maxPersistentRequestIDBytes), w.Body.String())
	require.Equal(t, w.Body.String(), w.Header().Get(clientRequestIDHeader))
}

func TestClientRequestIDPreservesExistingContextID(t *testing.T) {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.Use(ClientRequestID())
	router.GET("/", func(c *gin.Context) {
		value, _ := c.Request.Context().Value(ctxkey.ClientRequestID).(string)
		c.String(http.StatusOK, value)
	})

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req = req.WithContext(context.WithValue(req.Context(), ctxkey.ClientRequestID, "existing-client-request-id"))
	router.ServeHTTP(w, req)

	require.Equal(t, http.StatusOK, w.Code)
	require.Equal(t, "existing-client-request-id", w.Body.String())
	require.Equal(t, "existing-client-request-id", w.Header().Get(clientRequestIDHeader))
}

func TestClientRequestIDAcceptsBoundedHeaderWithContextPrecedence(t *testing.T) {
	gin.SetMode(gin.TestMode)
	for _, tc := range []struct{ header, contextID, want string }{
		{"a60980ef-5549-452d-afba-21b94c392162", "", "a60980ef-5549-452d-afba-21b94c392162"},
		{"caller-id", "context-id", "context-id"},
		{strings.Repeat("x", 200), "", ""},
		{"", "", ""},
	} {
		router := gin.New()
		router.Use(ClientRequestID())
		router.GET("/", func(c *gin.Context) {
			c.String(http.StatusOK, c.Request.Context().Value(ctxkey.ClientRequestID).(string))
		})
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		req.Header.Set(clientRequestIDHeader, tc.header)
		if tc.contextID != "" {
			req = req.WithContext(context.WithValue(req.Context(), ctxkey.ClientRequestID, tc.contextID))
		}
		response := httptest.NewRecorder()
		router.ServeHTTP(response, req)
		if tc.want == "" {
			require.Len(t, response.Body.String(), 36)
		} else {
			require.Equal(t, tc.want, response.Body.String())
		}
		require.Equal(t, response.Body.String(), response.Header().Get(clientRequestIDHeader))
	}
}

func TestClientCorrelationDoesNotReuseBillingIdentityAcrossHTTPCalls(t *testing.T) {
	gin.SetMode(gin.TestMode)
	for _, header := range []string{"codex-conversation-id", "", strings.Repeat("x", 200), "invalid id"} {
		t.Run(header, func(t *testing.T) {
			var ids []string
			router := gin.New()
			router.Use(ClientRequestID(), func(c *gin.Context) {
				before := c.Request.Context().Value(ctxkey.UsageBillingRequestID)
				c.Next()
				require.Equal(t, before, c.Request.Context().Value(ctxkey.UsageBillingRequestID))
			}, ClientRequestID())
			router.POST("/responses", func(c *gin.Context) {
				id, _ := c.Request.Context().Value(ctxkey.UsageBillingRequestID).(string)
				ids = append(ids, id)
				c.Status(http.StatusOK)
			})
			for i := 0; i < 2; i++ {
				req := httptest.NewRequest(http.MethodPost, "/responses", nil)
				req.Header.Set(clientRequestIDHeader, header)
				response := httptest.NewRecorder()
				router.ServeHTTP(response, req)
				require.Equal(t, http.StatusOK, response.Code)
				if header == "codex-conversation-id" {
					require.Equal(t, header, response.Header().Get(clientRequestIDHeader))
				}
			}
			require.Len(t, ids[0], 36)
			require.Len(t, ids[1], 36)
			require.NotEqual(t, ids[0], ids[1])
		})
	}
}
