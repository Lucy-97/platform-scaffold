# response — 统一响应格式

## 概述

所有 API 返回统一的 JSON 结构：

```json
{
    "code": "200",
    "msg": "OK",
    "data": { ... }
}
```

与 Java `Response<T>` 三字段完全对齐。前端按 `code` 决定展示逻辑，`msg` 仅作开发期回退。

## API

```go
// 成功
response.OK(c, user)                    // {"code":"200","msg":"OK","data":{...}}
response.OKPage(c, list, totalSize)     // {"code":"200","msg":"OK","data":{"data":[...],"totalSize":42}}

// 业务错误 (HTTP 400)
response.BadRequest(c, "104001", "Insufficient points.")

// 鉴权 (HTTP 401)
response.Unauthorized(c, "100001", "Missing authorization header")

// 禁止/Token 过期 (HTTP 403)
response.Forbidden(c, "100002", "Access token expired, please refresh")

// 需付费 (HTTP 402)
response.PaymentRequired(c, "104003", "Payment required")

// 需订阅 (HTTP 406)
response.NotAcceptable(c, "104004", "Subscription required")

// 内部错误 (HTTP 500)
response.InternalError(c, "something went wrong")

// 自定义
response.Err(c, http.StatusConflict, "110001", "Order conflict")
```

## HTTP 状态码 vs 业务错误码

| 层 | 谁用 | 示例 |
|----|------|------|
| HTTP 状态码 | 基础设施层（网关/限流/中间件） | 401 未登录、403 过期、429 限流、500 panic |
| 业务错误码 (code) | 业务层（handler/service） | `104001` 积分不足、`110002` 订单已取消 |

**规则**：HTTP 200 + code 非 `"200"` = 业务错误；HTTP 4xx/5xx = 基础设施错误。

## 前端处理建议

```typescript
// axios interceptor
if (res.status === 200 && res.data.code === "200") {
    return res.data.data;
}
if (res.status === 403 && res.data.code === "100002") {
    // token 过期 → 自动 refresh
}
// 其他 → 按 code 查国际化表
showToast(i18n[res.data.code] || res.data.msg);
```

## 注意事项

- `code` 是 string 类型，不是 int——与 Java 端 `SoulsErrorCodeMessage.XXX` 对齐
- 成功的 code 固定为 `"200"`，不要用 `"0"` 或 `"000000"`
- `data` 在失败时为 `null`，不要返回错误详情
