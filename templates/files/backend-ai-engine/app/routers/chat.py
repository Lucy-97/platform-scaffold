"""AI 路由占位。

设计原则：
  - Python 端只读：收到请求 → 调用 LLM → 返回 SSE 流
  - 任何写操作（扣费、记录等）必须委托给 Go API（POST /internal/...）
  - 通过 X-User-UUID 头识别登录用户（由 gateway 注入）
"""
from fastapi import APIRouter, Header, HTTPException

router = APIRouter()


@router.post("/completions")
async def completions(
    payload: dict,
    x_user_uuid: str | None = Header(default=None, alias="X-User-UUID"),
):
    """AI 补全占位实现。"""
    if not x_user_uuid:
        raise HTTPException(status_code=401, detail="missing user identity")

    # TODO: 在此实现：
    # 1. 通过 httpx 调用 Go API /internal/... 校验配额
    # 2. 调用 LLM provider 生成回答
    # 3. 通过 httpx 调用 Go API /internal/... 持久化结果
    # 4. 返回 SSE / 普通 JSON
    return {"code": "200", "msg": "OK", "data": {"echo": payload, "user": x_user_uuid}}
