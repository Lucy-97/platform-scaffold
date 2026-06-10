# errcode — 六位业务错误码注册表

## 概述

所有业务错误使用 6 位字符串错误码（如 `"000001"`），与 HTTP 状态码解耦。前端按 code 做国际化映射，后端只需注册 code + 默认 msg。

## 错误码分段

| 段 | 范围 | 域 |
|----|------|----|
| 系统层 | `000xxx` | JWT / 鉴权基础设施 / 内部错误 |
| 鉴权与注册 | `100xxx` | 登录 / 注册 / 验证码 |
| 文件与资源 | `103xxx` | 上传 / 下载 |
| 支付与计费 | `104xxx` | 扣费 / 充值 / 订阅 |
| 基础设施 | `105xxx` | 外部服务 / 分布式锁 |
| 业务预留 | `11xxxx` ~ `99xxxx` | 新业务模块自取 |

## API

```go
// 注册一个新错误码（通常在包级 var 集中声明）
e := errcode.New("110001", "Order not found.")

// 实现 error 接口
fmt.Println(e.Error()) // [110001] Order not found.

// 携带运行时上下文（不修改 code/msg）
wrapped := e.Wrap("order_id=abc-123")
fmt.Println(wrapped.Error()) // [110001] Order not found. (order_id=abc-123)
```

## 与 response 包配合

```go
// handler 层
func (h *Handler) GetOrder(c *gin.Context) {
    order, err := h.svc.FindOrder(...)
    if err != nil {
        var ec errcode.ErrorCode
        if errors.As(err, &ec) {
            response.BadRequest(c, ec.Code, ec.Msg)
            return
        }
        response.InternalError(c, "unknown error")
        return
    }
    response.OK(c, order)
}
```

## 新增业务错误码

在业务包内声明全局变量即可：

```go
// internal/service/order/errcodes.go
var (
    ErrOrderNotFound  = errcode.New("110001", "Order not found.")
    ErrOrderCancelled = errcode.New("110002", "Order has been cancelled.")
)
```

## 注意事项

- **不要**跳过注册直接硬编码 `c.JSON(400, gin.H{"code": "999999", ...})`——前端无法翻译
- **不要**复用已有 code——code 是前端国际化 key，改了就是 breaking change
- `WrappedError.Detail` 仅用于服务端日志，**不会**返回给前端
