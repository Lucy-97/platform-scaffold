"""统一响应格式。与 Go 端 pkg-platform-core/response 三字段对齐。"""
from typing import Any, Optional

from pydantic import BaseModel


class R(BaseModel):
    code: str = "200"
    msg: str = "OK"
    data: Optional[Any] = None


def ok(data: Any = None) -> dict:
    return R(code="200", msg="OK", data=data).model_dump()


def err(code: str, msg: str) -> dict:
    return R(code=code, msg=msg, data=None).model_dump()
