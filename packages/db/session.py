"""异步 SQLAlchemy 引擎与请求级 Session。

FastAPI 依赖 ``get_db`` 每个请求一个 Session，结束时关闭。
Worker 归档则直接用 ``AsyncSessionLocal()`` 上下文。
"""

from collections.abc import AsyncGenerator
import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from common.config import get_settings

_settings = get_settings()
_url = _settings.database_url
if "charset=" not in _url:
    _url = f"{_url}{'&' if '?' in _url else '?'}charset=utf8mb4"

engine = create_async_engine(
    _url,
    echo=_settings.debug,
    pool_pre_ping=True,
)
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

logger = logging.getLogger("db.session")


async def wait_for_db(*, attempts: int = 10) -> None:
    """宿主机 Docker 端口转发偶发未就绪：握手包为空会报 2013。"""
    last: BaseException | None = None
    for i in range(1, attempts + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            logger.warning("mysql not ready attempt=%s/%s: %s", i, attempts, exc)
            await asyncio.sleep(min(1.2 * i, 6))
    raise last or RuntimeError("mysql unavailable")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
