// Package middleware 提供 API 服务通用的 Gin 中间件。
//
// 包含：
//   - RequestID: 全链路请求 ID 生成/透传（与 gateway 透传同一个头）
//   - InternalAuth: X-Internal-Secret 校验，确认请求来自网关
//   - PrometheusMetrics: http_requests_total/duration/in_flight 指标
//
// 注意：JWT 解析在网关完成，API 只从 X-User-UUID 头读取身份，因此这里不含 JWT 中间件。
package middleware

import (
	"crypto/rand"
	"crypto/subtle"
	"fmt"
	"net/http"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

// ---------------- RequestID ----------------

const RequestIDHeader = "X-Request-ID"

// RequestID 生成或透传 X-Request-ID，写入 c.Set("requestID") 与响应头。
func RequestID() gin.HandlerFunc {
	return func(c *gin.Context) {
		id := c.GetHeader(RequestIDHeader)
		if id == "" {
			id = newUUIDv4()
		}
		c.Set("requestID", id)
		c.Header(RequestIDHeader, id)
		c.Next()
	}
}

func newUUIDv4() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	return fmt.Sprintf("%08x-%04x-%04x-%04x-%012x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}

// ---------------- InternalAuth ----------------

// InternalAuth 校验 X-Internal-Secret 请求头，确认请求来自网关。
// secret 为空时跳过验证（开发环境）。
func InternalAuth(secret string) gin.HandlerFunc {
	return func(c *gin.Context) {
		if secret == "" {
			c.Next()
			return
		}
		provided := c.GetHeader("X-Internal-Secret")
		if subtle.ConstantTimeCompare([]byte(provided), []byte(secret)) != 1 {
			c.AbortWithStatusJSON(http.StatusForbidden, gin.H{
				"code": "000403", "msg": "forbidden: invalid internal secret",
			})
			return
		}
		c.Next()
	}
}

// ---------------- Prometheus Metrics ----------------

var (
	httpRequestsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "http_requests_total",
			Help: "Total number of HTTP requests by method, path, and status.",
		},
		[]string{"method", "path", "status"},
	)

	httpRequestDuration = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "http_request_duration_seconds",
			Help:    "HTTP request latency distribution.",
			Buckets: []float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10},
		},
		[]string{"method", "path"},
	)

	httpRequestsInFlight = promauto.NewGauge(
		prometheus.GaugeOpts{
			Name: "http_requests_in_flight",
			Help: "Current number of HTTP requests being processed.",
		},
	)
)

// PrometheusMetrics 记录 HTTP 请求指标。
func PrometheusMetrics() gin.HandlerFunc {
	return func(c *gin.Context) {
		path := c.FullPath()
		if path == "" {
			path = "unmatched"
		}
		httpRequestsInFlight.Inc()
		start := time.Now()

		c.Next()

		httpRequestsInFlight.Dec()
		status := strconv.Itoa(c.Writer.Status())
		httpRequestsTotal.WithLabelValues(c.Request.Method, path, status).Inc()
		httpRequestDuration.WithLabelValues(c.Request.Method, path).Observe(time.Since(start).Seconds())
	}
}
