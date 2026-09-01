"""全局 JWT 校验：白名单外请求必须带有效 access token。

纯 ASGI 中间件（不用 BaseHTTPMiddleware），避免 SSE 流式响应触发
anyio cancel scope 错乱。
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from common.errors import ErrorCode
from common.logging import get_logger
from common.response import fail
from common.security import decode_token

logger = get_logger(__name__)

_EXACT_WHITELIST = frozenset(
    {
        "/health",
        "/api/v1/health",
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/openapi.json",
        "/docs",
        "/redoc",
    }
)

_PREFIX_WHITELIST = (
    "/docs/",
    "/redoc/",
)


def _is_onlyoffice_public(path: str) -> bool:
    """OnlyOffice Document Server 回调与拉取 docx，不能带用户 JWT。"""
    return path.endswith("/onlyoffice-callback") or "/onlyoffice-download" in path


def _is_whitelisted(path: str) -> bool:
    if path in _EXACT_WHITELIST:
        return True
    if _is_onlyoffice_public(path):
        return True
    return any(path.startswith(p) for p in _PREFIX_WHITELIST)


def access_token_from_headers(headers) -> str:
    """读 Bearer，或代理可能丢掉 Authorization 时的 X-Access-Token。"""
    auth = ""
    extra = ""
    try:
        auth = headers.get("Authorization") or headers.get("authorization") or ""
        extra = headers.get("X-Access-Token") or headers.get("x-access-token") or ""
    except Exception:
        auth = ""
        extra = ""
    if isinstance(auth, (list, tuple)):
        auth = auth[0] if auth else ""
    if isinstance(extra, (list, tuple)):
        extra = extra[0] if extra else ""
    auth = str(auth or "")
    extra = str(extra or "").strip()
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        if token:
            return token
    return extra


class JwtAuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        if request.method == "OPTIONS" or _is_whitelisted(request.url.path):
            await self.app(scope, receive, send)
            return

        auth = request.headers.get("Authorization") or ""
        token = access_token_from_headers(request.headers)
        if not token:
            logger.warning(
                "jwt missing token path=%s method=%s has_authorization=%s",
                request.url.path,
                request.method,
                bool(auth),
            )
            response = JSONResponse(
                status_code=401,
                content=fail(ErrorCode.UNAUTHORIZED, "missing access token"),
            )
            await response(scope, receive, send)
            return
        try:
            payload = decode_token(token)
        except ValueError:
            logger.warning("jwt invalid token path=%s method=%s", request.url.path, request.method)
            response = JSONResponse(
                status_code=401,
                content=fail(ErrorCode.UNAUTHORIZED, "invalid access token"),
            )
            await response(scope, receive, send)
            return
        if payload.get("type") != "access":
            response = JSONResponse(
                status_code=401,
                content=fail(ErrorCode.UNAUTHORIZED, "token type must be access"),
            )
            await response(scope, receive, send)
            return
        sub = payload.get("sub")
        if not sub:
            response = JSONResponse(
                status_code=401,
                content=fail(ErrorCode.UNAUTHORIZED, "invalid token subject"),
            )
            await response(scope, receive, send)
            return
        request.state.jwt_sub = sub
        await self.app(scope, receive, send)
