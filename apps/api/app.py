"""FastAPI 应用工厂。

中间件顺序（后 add 的在最外层）：
  CORS → OperationLog → JWT
即：先过 CORS，再记日志，最后 JWT。保证 401 响应也带 CORS 头。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging

from api.middleware import JwtAuthMiddleware, OperationLogMiddleware
from api.routers import (
    agents,
    ai,
    audit,
    auth,
    health,
    knowledge,
    layouts,
    mcp_servers,
    models,
    prompts,
    roles,
    tenders,
    users,
    workflows,
)
from common import __version__
from common.config import get_settings
from common.errors import AppError
from common.logging import setup_logging
from common.response import fail


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """启动时滚动清理过期审计日志，避免库无限膨胀。"""
    log = logging.getLogger("api.app")
    try:
        from db.session import wait_for_db

        await wait_for_db()
    except Exception:  # noqa: BLE001
        log.exception("mysql unavailable at startup; skip maintenance jobs")
        yield
        return
    try:
        from api.services import audit as audit_svc

        deleted = await audit_svc.purge_expired_logs()
        log.info(
            "audit purge on startup operations=%s logins=%s",
            deleted.get("operations", 0),
            deleted.get("logins", 0),
        )
    except Exception:  # noqa: BLE001
        log.exception("audit purge on startup failed")
    try:
        from api.services.knowledge.ingest import fail_stale_parsing_documents
        from api.services.knowledge.queue import start_ingest_worker

        n = await fail_stale_parsing_documents()
        log.info("kb stale parsing marked failed count=%s", n)
        start_ingest_worker()
    except Exception:  # noqa: BLE001
        log.exception("kb stale parsing reclaim failed")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging("DEBUG" if settings.debug else "INFO")

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="伟泰智能体交互系统 - 主业务 API",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=_lifespan,
    )

    app.add_middleware(JwtAuthMiddleware)
    app.add_middleware(OperationLogMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            logging.getLogger("api.app").error("AppError %s: %s", exc.status_code, exc.msg)
        elif exc.status_code >= 400:
            logging.getLogger("api.app").warning("AppError %s: %s", exc.status_code, exc.msg)
        return JSONResponse(
            status_code=exc.status_code,
            content=fail(exc.code, exc.msg),
        )

    app.include_router(health.router)
    app.include_router(health.router, prefix=settings.api_prefix)
    app.include_router(auth.router, prefix=settings.api_prefix)
    app.include_router(users.router, prefix=settings.api_prefix)
    app.include_router(roles.router, prefix=settings.api_prefix)
    app.include_router(audit.router, prefix=settings.api_prefix)
    app.include_router(models.router, prefix=settings.api_prefix)
    app.include_router(prompts.router, prefix=settings.api_prefix)
    app.include_router(mcp_servers.router, prefix=settings.api_prefix)
    app.include_router(agents.router, prefix=settings.api_prefix)
    app.include_router(knowledge.router, prefix=settings.api_prefix)
    app.include_router(layouts.router, prefix=settings.api_prefix)
    app.include_router(tenders.router, prefix=settings.api_prefix)
    app.include_router(workflows.router, prefix=settings.api_prefix)
    app.include_router(ai.router, prefix=settings.api_prefix)

    return app


app = create_app()
