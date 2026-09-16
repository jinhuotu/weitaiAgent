"""日志保留天数与过期文件清理。"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from common.config import get_settings
from common.logging import purge_expired_log_files


def test_purge_expired_log_files_keeps_recent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_RETENTION_DAYS", "7")
    get_settings.cache_clear()
    today = datetime.now().date()
    keep = tmp_path / f"weitai-agent.{today:%Y-%m-%d}.log"
    old = tmp_path / f"weitai-agent.{(today - timedelta(days=7)):%Y-%m-%d}.log"
    keep.write_text("keep", encoding="utf-8")
    old.write_text("old", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("no", encoding="utf-8")
    n = purge_expired_log_files(log_dir=tmp_path, days=7)
    assert n == 1
    assert keep.exists()
    assert not old.exists()
    assert (tmp_path / "notes.txt").exists()
    get_settings.cache_clear()


def test_audit_retention_follows_log_days_when_audit_zero(monkeypatch) -> None:
    from api.services import audit as audit_svc

    monkeypatch.setenv("LOG_RETENTION_DAYS", "14")
    monkeypatch.setenv("AUDIT_LOG_RETENTION_DAYS", "0")
    get_settings.cache_clear()
    assert audit_svc.retention_days() == 14
    monkeypatch.setenv("AUDIT_LOG_RETENTION_DAYS", "3")
    get_settings.cache_clear()
    assert audit_svc.retention_days() == 3
    get_settings.cache_clear()
