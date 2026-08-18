"""进程级日志初始化。"""

import logging
import sys
from typing import Any


def setup_logging(level: str = "INFO") -> None:
    """覆盖 root handler，保证 uvicorn 子进程也能打到 stdout。"""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def bind_request_id(logger: logging.Logger, request_id: str) -> logging.LoggerAdapter[Any]:
    """预留：请求追踪尚未全链路落地，先提供 Adapter 入口。"""
    return logging.LoggerAdapter(logger, {"request_id": request_id})
