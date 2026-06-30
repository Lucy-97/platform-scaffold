// Package errcode 提供六位业务错误码注册表。
//
// 设计:
//   - 错误码六位字符串，按业务域分段（000xxx 系统层 / 100xxx 鉴权 / 103xxx 资源 / 105xxx 基础设施 等）
//   - 与 HTTP 状态码解耦：业务错误统一返回 HTTP 400 + code，鉴权返回 401/403，订阅返回 406
//   - 通过 ErrorCode.Wrap() 携带运行时上下文，但 Code/Msg 始终来自注册表
package errcode

import "fmt"

// ErrorCode 一对 code+msg。Code 是面向前端的稳定契约，Msg 仅作开发期回退提示，
// 真实展示文案由前端按 Code 翻译。
type ErrorCode struct {
	Code string
	Msg  string
}

// New 创建一个新的错误码。建议在各业务包内集中声明全局变量。
func New(code, msg string) ErrorCode {
	return ErrorCode{Code: code, Msg: msg}
}

// Error 实现 error 接口。
func (e ErrorCode) Error() string {
	return fmt.Sprintf("[%s] %s", e.Code, e.Msg)
}

// Wrap 在错误码上挂载运行时上下文（不修改 Code/Msg）。
func (e ErrorCode) Wrap(detail string) WrappedError {
	return WrappedError{Code: e.Code, Msg: e.Msg, Detail: detail}
}

// WrappedError 承载完整错误信息：code、面向用户的 msg、以及开发期诊断 detail。
type WrappedError struct {
	Code   string
	Msg    string
	Detail string
}

func (w WrappedError) Error() string {
	if w.Detail == "" {
		return fmt.Sprintf("[%s] %s", w.Code, w.Msg)
	}
	return fmt.Sprintf("[%s] %s (%s)", w.Code, w.Msg, w.Detail)
}

// ============================================================
// 通用错误码（业务无关，所有项目都会用到的部分）
// ============================================================

var (
	// 系统层 000xxx
	ErrJWTInvalid     = New("000001", "Token is invalid.")
	ErrTokenExpired   = New("000002", "Token has expired.")
	ErrUnauthorized   = New("000004", "Authentication is required. Please log in.")
	ErrNoPermission   = New("000005", "Permission denied to access this resource.")
	ErrServiceBusy    = New("000006", "Service is busy, please try again later.")
	ErrParamMissing   = New("000007", "Parameter is missing.")
	ErrInternalServer = New("000099", "Internal server error.")

	// 资源 103xxx
	ErrNotFound = New("103404", "Resource not found.")

	// 基础设施 105xxx
	ErrExternalServiceFailed = New("105001", "External service request failed. Please try again later.")
	ErrTryLockFailed         = New("105002", "Failed to acquire lock, please try again later.")

	// TODO: 按你的业务域追加错误码，建议分段：
	//   100xxx — 鉴权与注册
	//   104xxx — 支付与计费
	//   11xxxx ~ 99xxxx — 各业务模块
	// 示例：
	//   ErrOrderNotFound  = New("110001", "Order not found.")
	//   ErrOrderCancelled = New("110002", "Order has been cancelled.")
)
