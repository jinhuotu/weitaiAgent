"""进程级日志：stdout + 按天文件，超期文件滚动删掉。"""

from __future__ import annotations

import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_LOG_DATE_RE = re.compile(r"^weitai-agent\.(\d{4}-\d{2}-\d{2})\.log$")


class DailyFileHandler(logging.FileHandler):
    """按本地日期写 weitai-agent.YYYY-MM-DD.log，避免 Windows 上 rename 锁文件。"""

    def __init__(self, directory: Path, prefix: str = "weitai-agent"):
        self.directory = Path(directory)
        self.prefix = prefix
        self._stamp = datetime.now().strftime("%Y-%m-%d")
        self.directory.mkdir(parents=True, exist_ok=True)
        super().__init__(str(self.directory / f"{self.prefix}.{self._stamp}.log"), encoding="utf-8")

    def emit(self, record: logging.LogRecord) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d")
        if stamp != self._stamp:
            self.acquire()
            try:
                if stamp != self._stamp:
                    if self.stream:
                        self.stream.close()
                        self.stream = None  # type: ignore[assignment]
                    self._stamp = stamp
                    self.baseFilename = str(self.directory / f"{self.prefix}.{stamp}.log")
                    self.stream = self._open()
            finally:
                self.release()
        super().emit(record)


def log_retention_days() -> int:
    from common.config import get_settings

    days = int(get_settings().log_retention_days or 7)
    return max(1, days)


def purge_expired_log_files(
    *,
    log_dir: str | Path | None = None,
    days: int | None = None,
) -> int:
    from common.config import get_settings

    settings = get_settings()
    root = Path(log_dir or settings.log_dir)
    keep = max(1, int(days if days is not None else settings.log_retention_days or 7))
    if not root.is_dir():
        return 0
    today = datetime.now().date()
    removed = 0
    for p in root.iterdir():
        if not p.is_file():
            continue
        m = _LOG_DATE_RE.match(p.name)
        if not m:
            continue
        try:
            day = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if (today - day).days >= keep:
            p.unlink(missing_ok=True)
            removed += 1
    return removed


def setup_logging(level: str = "INFO") -> None:
    """覆盖 root handler：stdout + logs/weitai-agent.日期.log。"""
    from common.config import get_settings

    settings = get_settings()
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    try:
        n = purge_expired_log_files(log_dir=log_dir, days=settings.log_retention_days)
        if n:
            print(f"purged {n} expired log files from {log_dir}", file=sys.stderr)
    except OSError:
        pass

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    file_h = DailyFileHandler(log_dir)
    file_h.setFormatter(fmt)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=[stream, file_h],
        force=True,
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def bind_request_id(logger: logging.Logger, request_id: str) -> logging.LoggerAdapter[Any]:
    return logging.LoggerAdapter(logger, {"request_id": request_id})
