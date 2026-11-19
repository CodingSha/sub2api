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
