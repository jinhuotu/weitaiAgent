"""uvicorn 入口。Windows 下关闭 --reload，避免旧进程占端口导致新接口 404。"""

import logging
import sys

import uvicorn

from common.config import get_settings


def run() -> None:
    settings = get_settings()
    use_reload = bool(settings.debug) and sys.platform != "win32"
    reload_dirs = None
    if use_reload:
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        reload_dirs = [
            str(root / "apps" / "api"),
            str(root / "packages"),
        ]
    if settings.debug and not use_reload:
        logging.getLogger("api.main").warning(
            "Windows 已关闭 uvicorn --reload，避免 %s 上残留旧进程导致新接口 404；改代码后请重启 API",
            settings.api_port,
        )
    uvicorn.run(
        "api.app:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=use_reload,
        reload_dirs=reload_dirs,
        reload_delay=1.0,
        reload_excludes=[
            ".venv/*",
            ".venv\\*",
            "venv/*",
            "venv\\*",
            "**/site-packages/**",
            "**/.pytest_cache/**",
            "**/__pycache__/**",
            "**/*.pyc",
            "**/alembic/versions/**",
        ],
    )


if __name__ == "__main__":
    run()
