"""统一业务异常。

与 Go 端 errcode 包对齐：6 位 code + msg + 可选 detail。
通过 BizException + 全局 handler 把异常映射到 {code, msg, data:null} 响应。
"""
from typing import Optional


class BizException(Exception):
    """业务异常。HTTP 状态码默认 400，可在子类或抛出时覆盖。"""

    http_status: int = 400

    def __init__(self, code: str, msg: str, detail: Optional[str] = None):
        self.code = code
        self.msg = msg
        self.detail = detail
        super().__init__(msg)


class UnauthorizedException(BizException):
    http_status = 401


class ForbiddenException(BizException):
    http_status = 403


class PaymentRequiredException(BizException):
    http_status = 402
