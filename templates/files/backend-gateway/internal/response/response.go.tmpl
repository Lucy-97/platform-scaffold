// Package response 统一响应格式 {code, msg, data}。
//
// HTTP 状态码语义：
//
//	200  成功
//	400  业务错误
//	401  未登录
//	402  需要付费
//	403  Forbidden / token 过期
//	406  需要订阅
//	500  服务端错误
package response

import (
	"net/http"

	"github.com/gin-gonic/gin"
)

// R 统一响应体。
type R struct {
	Code string `json:"code"`
	Msg  string `json:"msg"`
	Data any    `json:"data"`
}

// OK 200 + data。
func OK(c *gin.Context, data any) {
	c.JSON(http.StatusOK, R{Code: "200", Msg: "OK", Data: data})
}

// OKPage 200 + 分页 data + totalSize。
func OKPage(c *gin.Context, data any, totalSize int64) {
	c.JSON(http.StatusOK, R{
		Code: "200", Msg: "OK",
		Data: gin.H{"data": data, "totalSize": totalSize},
	})
}

// Err 自定义状态码。
func Err(c *gin.Context, httpStatus int, code, msg string) {
	c.JSON(httpStatus, R{Code: code, Msg: msg, Data: nil})
}

// BadRequest 400 业务错误。
func BadRequest(c *gin.Context, code, msg string) { Err(c, http.StatusBadRequest, code, msg) }

// Unauthorized 401。
func Unauthorized(c *gin.Context, code, msg string) {
	Err(c, http.StatusUnauthorized, code, msg)
}

// Forbidden 403。
func Forbidden(c *gin.Context, code, msg string) {
	Err(c, http.StatusForbidden, code, msg)
}

// PaymentRequired 402。
func PaymentRequired(c *gin.Context, code, msg string) {
	Err(c, http.StatusPaymentRequired, code, msg)
}

// NotAcceptable 406 (订阅必需)。
func NotAcceptable(c *gin.Context, code, msg string) {
	Err(c, http.StatusNotAcceptable, code, msg)
}

// InternalError 500。
func InternalError(c *gin.Context, msg string) {
	Err(c, http.StatusInternalServerError, "500", msg)
}
