# middleware — Gin 中间件集合

## 概述

6 个通用中间件，被 `backend-gateway` 和 `backend-api` 共用。

| 中间件 | 用途 | 典型使用方 |
|--------|------|-----------|
| `RequestID` | 全链路请求 ID | Gateway + API |
| `CORS` | 跨域白名单 | Gateway |
| `JWT` | Bearer Token 校验 | Gateway |
| `InternalAuth` | X-Internal-Secret 校验 | API + AI Engine |
| `RateLimit` | Redis 固定窗口限流 | Gateway |
| `PrometheusMetrics` | HTTP 指标采集 | Gateway + API |

---

## RequestID

生成或透传 `X-Request-ID`，写入 `c.Set("requestID")` 和响应头。

```go
r.Use(middleware.RequestID())
```

- 自带 UUID v4 生成器，无外部依赖
- 如果上游已传 `X-Request-ID`，则透传不覆盖

---

## CORS

白名单 origin + `AllowCredentials`。

```go
r.Use(middleware.CORS(
    []string{"https://myapp.ai", "http://localhost:3000"},
    "X-Custom-Header", // 可选 extraHeaders
))
```

- 不在白名单的 origin 不会返回 `Access-Control-Allow-Origin`
- 自动处理 `OPTIONS` 预检请求
- `Allow-Credentials: true`（前端可带 cookie）

---

## JWT

Bearer Token 校验 + 公开路径白名单 + 过期返回 403（触发前端 refresh）。

```go
// 业务方需实现 JWTValidator 接口
type JWTValidator interface {
    ValidateToken(token string) (middleware.Claims, error)
}

// Gateway 中的用法
jwtManager := gwjwt.NewManager(cfg.JWT.Secret, cfg.JWT.AccessExpSec)
r.Use(middleware.JWT(jwtManager, middleware.JWTOptions{
    PublicPathPrefixes:     []string{"/api/auth/", "/api/public/", "/health"},
    RefreshTokenCookieName: "refreshToken_",
}))
```

### 行为矩阵

| 场景 | 行为 |
|------|------|
| 公开路径 + 无 token | 放行，不注入身份头 |
| 公开路径 + 有 token | 放行，尝试解析并注入身份头（忽略解析失败） |
| 受保护路径 + 无 token | 401 `Missing or invalid authorization header` |
| 受保护路径 + 无 token + 有 refresh cookie | 403 `Access token expired`（前端据此自动 refresh） |
| 受保护路径 + token 过期 | 403 `Access token expired` |
| 受保护路径 + token 无效 | 401 `Invalid access token` |
| 受保护路径 + token 合法 | 放行，注入 `X-User-UUID` / `X-Member-Level` |

### 实现 JWTValidator

```go
// backend-gateway/pkg/jwt/jwt.go
type Manager struct { secret string; expSec int }
func (m *Manager) ValidateToken(tokenStr string) (middleware.Claims, error) {
    claims, expired, err := m.parse(tokenStr)
    if expired {
        return middleware.Claims{Expired: true}, ErrTokenExpired
    }
    if err != nil {
        return middleware.Claims{}, err
    }
    return middleware.Claims{UserUUID: claims.UUID, MemberLevel: claims.Level}, nil
}
```

---

## InternalAuth

校验 `X-Internal-Secret`，保护 `/internal/*` 私域路由。

```go
r.Use(middleware.InternalAuth(cfg.InternalAPISecret))
```

- `secret == ""` 时跳过验证（开发环境）
- 使用 `subtle.ConstantTimeCompare` 防时序攻击
- 校验失败返回 403 `forbidden: invalid internal secret`

---

## RateLimit

Redis 固定窗口限流，按用户 UUID 或 IP 限流。

```go
rl := middleware.NewRedisRateLimiter(rdb, 60, time.Minute) // 每分钟 60 次
r.Use(middleware.RateLimit(rl))
```

- 登录用户按 UUID 限流（key: `RATE:USER:<uuid>:<slot>`）
- 未登录用户按 IP 限流（key: `RATE:IP:<ip>:<slot>`）
- Redis 错误时 **fail-open**（放行）
- 超限返回 429 `Too many requests`

---

## PrometheusMetrics

采集 HTTP 请求指标。

```go
r.Use(middleware.PrometheusMetrics())
```

| 指标 | 类型 | Labels |
|------|------|--------|
| `http_requests_total` | Counter | method, path, status |
| `http_request_duration_seconds` | Histogram | method, path |
| `http_requests_in_flight` | Gauge | — |

Histogram buckets: 5ms, 10ms, 25ms, 50ms, 100ms, 250ms, 500ms, 1s, 2.5s, 5s, 10s

---

## Gateway 中间件链顺序

```go
r.Use(gin.Recovery())
r.Use(middleware.CORS(allowedOrigins))
r.Use(middleware.RequestID())
r.Use(middleware.PrometheusMetrics())
r.Use(middleware.JWT(jwtManager, jwtOpts))
r.Use(middleware.RateLimit(rl))
```

## API 中间件链顺序

```go
r.Use(gin.Recovery())
r.Use(middleware.RequestID())
r.Use(middleware.PrometheusMetrics())
r.Use(middleware.InternalAuth(cfg.InternalAPISecret))
```

## 注意事项

- `JWT` 中间件不依赖具体 JWT 实现，通过 `JWTValidator` 接口解耦
- `InternalAuth` 应在 API 侧挂在**全局**，而不是单个路由——否则容易遗漏
- `RateLimit` 放在 `JWT` 之后，这样 `c.GetString("userUUID")` 才能取到值
- `PrometheusMetrics` 尽量靠前，捕获所有请求（包括被中间件拦截的）
