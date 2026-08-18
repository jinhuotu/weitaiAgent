"""统一 JSON 响应包：前端约定 ``code === 0`` 为成功。"""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一响应：{ code, msg, data }。"""

    code: int = 0
    msg: str = "ok"
    data: T | None = None


class PageData(BaseModel, Generic[T]):
    """分页结构（列表接口可按需使用）。"""

    items: list[T] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20


def ok(data: Any = None, msg: str = "ok") -> dict[str, Any]:
    """成功响应。"""
    return {"code": 0, "msg": msg, "data": data}


def fail(code: int, msg: str, data: Any = None) -> dict[str, Any]:
    """失败响应。HTTP 状态码由路由/异常处理器另行设置。"""
    return {"code": code, "msg": msg, "data": data}
