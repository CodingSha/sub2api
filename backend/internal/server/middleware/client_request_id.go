package middleware

import (
	"context"
	"strings"

	"github.com/Wei-Shaw/sub2api/internal/pkg/ctxkey"
	"github.com/Wei-Shaw/sub2api/internal/pkg/logger"
	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"go.uber.org/zap"
)

const clientRequestIDHeader = "X-Client-Request-ID"

// ClientRequestID 保留调用方关联标识，并为每次入站调用分配独立落账标识。
func ClientRequestID() gin.HandlerFunc {
	return func(c *gin.Context) {
		if c.Request == nil {
			c.Next()
			return
		}

		// 客户端可跨请求复用关联头；落账身份只在服务端生成，同请求续跑保持不变。
		ctx := c.Request.Context()
		if id, _ := ctx.Value(ctxkey.UsageBillingRequestID).(string); strings.TrimSpace(id) == "" {
			ctx = context.WithValue(ctx, ctxkey.UsageBillingRequestID, uuid.NewString())
		}
		c.Request = c.Request.WithContext(ctx)

		if v, _ := c.Request.Context().Value(ctxkey.ClientRequestID).(string); strings.TrimSpace(v) != "" {
			var valid bool
			v, valid = normalizeCorrelationID(v)
			if !valid {
				v = uuid.New().String()
			}
			c.Header(clientRequestIDHeader, v)
			ctx := context.WithValue(c.Request.Context(), ctxkey.ClientRequestID, v)
			c.Request = c.Request.WithContext(ctx)
			c.Next()
			return
		}

		// 保留合法的调用方关联标识，便于对账；它不承担鉴权或幂等判定。
		id, valid := normalizeCorrelationID(c.GetHeader(clientRequestIDHeader))
		if !valid {
			id = uuid.New().String()
		}
		c.Header(clientRequestIDHeader, id)
		ctx = context.WithValue(c.Request.Context(), ctxkey.ClientRequestID, id)
		requestLogger := logger.FromContext(ctx).With(zap.String("client_request_id", strings.TrimSpace(id)))
		ctx = logger.IntoContext(ctx, requestLogger)
		c.Request = c.Request.WithContext(ctx)
		c.Next()
	}
}
