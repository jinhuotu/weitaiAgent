"""写入类请求的操作日志中间件（用户/角色/模型/工作流）。

纯 ASGI 实现，不包一层 BaseHTTPMiddleware 任务；SSE 试跑/对话不在此记日志。
"""

from __future__ import annotations

import time

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.services import audit as audit_svc
from common.logging import get_logger

logger = get_logger(__name__)


def _is_sse_path(method: str, path: str) -> bool:
    if (method or "").upper() != "POST":
        return False
    raw = (path or "").split("?", 1)[0].rstrip("/")
    return raw.endswith("/chat") or raw.endswith("/runs")


class OperationLogMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        if request.method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        path = request.url.path
        method = request.method.upper()
        if _is_sse_path(method, path):
            await self.app(scope, receive, send)
            return

        target = audit_svc.match_write_target(method, path)
        if target is None:
            await self.app(scope, receive, send)
            return

        module, action, resource_id = target
        started = time.perf_counter()
        ip = audit_svc.client_ip(request)
        ua = audit_svc.user_agent(request)
        status_box = {"code": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_box["code"] = int(message.get("status") or 500)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            await _persist(
                module=module,
                action=action,
                method=method,
                path=path,
                resource_id=resource_id,
                request=request,
                ip=ip,
                ua=ua,
                status_code=500,
                error_msg="unhandled exception",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            raise

        await _persist(
            module=module,
            action=action,
            method=method,
            path=path,
            resource_id=resource_id,
            request=request,
            ip=ip,
            ua=ua,
            status_code=int(status_box["code"] or 500),
            error_msg=None,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


async def _persist(
    *,
    module: str,
    action: str,
    method: str,
    path: str,
    resource_id: str | None,
    request: Request,
    ip: str,
    ua: str,
    status_code: int,
    error_msg: str | None,
    duration_ms: int,
) -> None:
    operator = getattr(request.state, "jwt_sub", None)
    try:
        await audit_svc.record_operation(
            module=module,
            action=action,
            method=method,
            path=path,
            resource_id=resource_id,
            operator_username=str(operator) if operator else None,
            success=status_code < 400,
            status_code=status_code,
            ip=ip,
            user_agent_str=ua,
            error_msg=error_msg,
            duration_ms=duration_ms,
        )
    except Exception:  # noqa: BLE001
        logger.exception("operation log persist failed path=%s", path)
