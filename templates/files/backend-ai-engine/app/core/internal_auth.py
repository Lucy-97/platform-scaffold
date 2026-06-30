"""X-Internal-Secret 校验中间件，保护 AI Engine 不被外部直接访问。

与 Go 端 backend-api/internal/middleware.InternalAuth 行为对齐：
secret 为空时跳过（开发环境）；非空时使用恒定时间比较。
"""
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# 不需要鉴权的路径前缀（健康检查、OpenAPI 文档等）。
PUBLIC_PREFIXES = ("/health", "/metrics", "/docs", "/redoc", "/openapi.json")


class InternalAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, secret: str):
        super().__init__(app)
        self.secret = secret or ""

    async def dispatch(self, request: Request, call_next):
        if not self.secret:
            return await call_next(request)
        if any(request.url.path.startswith(p) for p in PUBLIC_PREFIXES):
            return await call_next(request)

        provided = request.headers.get("X-Internal-Secret", "")
        if not secrets.compare_digest(provided, self.secret):
            return JSONResponse(
                status_code=403,
                content={"code": "000403", "msg": "forbidden: invalid internal secret", "data": None},
            )
        return await call_next(request)
