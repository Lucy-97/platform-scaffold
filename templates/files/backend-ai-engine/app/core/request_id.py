"""请求 ID 中间件。

从 X-Request-ID 头读取（由 gateway 注入），存入 contextvar 方便日志使用；
若未提供则生成新的 uuid4。
"""
import uuid
from contextvars import ContextVar

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        rid = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        token = request_id_ctx.set(rid)
        try:
            resp = await call_next(request)
            resp.headers[REQUEST_ID_HEADER] = rid
            return resp
        finally:
            request_id_ctx.reset(token)


def current_request_id() -> str:
    return request_id_ctx.get("")
