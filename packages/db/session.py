"""异步 SQLAlchemy 引擎与请求级 Session。

FastAPI 依赖 ``get_db`` 每个请求一个 Session，结束时关闭。
Worker 归档则直接用 ``AsyncSessionLocal()`` 上下文。
"""

from collections.abc import AsyncGenerator

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


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
