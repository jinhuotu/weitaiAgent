"""待补附件槽位：按 key 上传扫描件，生成时插入 Word；无文件则画虚线框。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from api.services.tenders.assets import tender_assets_dir
from api.services.tenders.placeholders import DEFAULT_SLOTS, TECH_DRAWING_SLOT, collect_slots
from api.services.tenders.schema import PlaceholderItem
from common.errors import AppError, ErrorCode

_KEY_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
_DRAWING_ID_RE = re.compile(r"^[a-f0-9]{10,16}$", re.I)
_ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_MAX_BYTES = 40 * 1024 * 1024
_MAX_FILES_PER_SLOT = 30


def sanitize_slot_key(key: str) -> str:
    raw = (key or "").strip()
    if not _KEY_RE.match(raw):
        raise AppError(ErrorCode.VALIDATION, "无效的附件项 key", status_code=422)
    return raw


def slots_root() -> Path:
    path = tender_assets_dir() / "slots"
    path.mkdir(parents=True, exist_ok=True)
    return path


def slot_dir(key: str) -> Path:
    safe = sanitize_slot_key(key)
    path = slots_root() / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_slot_files(key: str) -> list[Path]:
    folder = slot_dir(key)
    files = [
        p
        for p in folder.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in _ALLOWED_EXT
    ]
    return sorted(files, key=lambda p: p.name.lower())


def clear_slot(key: str) -> int:
    folder = slot_dir(key)
    removed = 0
    for item in list(folder.iterdir()):
        if item.is_file():
            item.unlink(missing_ok=True)
            removed += 1
        elif item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
            removed += 1
    return removed


def save_slot_file(key: str, *, filename: str, data: bytes, replace: bool = False) -> dict[str, object]:
    if not data:
        raise AppError(ErrorCode.VALIDATION, "上传文件为空", status_code=422)
    if len(data) > _MAX_BYTES:
        raise AppError(ErrorCode.VALIDATION, "单个文件不能超过 40MB", status_code=422)

    name = Path((filename or "upload.bin").replace("\\", "/").split("/")[-1]).name
    suffix = Path(name).suffix.lower()
    if suffix not in _ALLOWED_EXT:
        raise AppError(
            ErrorCode.VALIDATION,
            "仅支持 PDF / PNG / JPG / WEBP / GIF / BMP",
            status_code=422,
        )

    if replace:
        clear_slot(key)

    existing = list_slot_files(key)
    if len(existing) >= _MAX_FILES_PER_SLOT:
        raise AppError(ErrorCode.VALIDATION, f"每项最多 {_MAX_FILES_PER_SLOT} 个文件", status_code=422)

    stem = Path(name).stem[:80] or "scan"
    stem = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", stem).strip("_") or "scan"
    dest_name = f"{stem}{suffix}"
    dest = slot_dir(key) / dest_name
    n = 1
    while dest.exists():
        dest = slot_dir(key) / f"{stem}_{n}{suffix}"
        n += 1
    dest.write_bytes(data)
    files = list_slot_files(key)
    safe = sanitize_slot_key(key)
    meta = _slot_meta(safe)
    return {
        "key": safe,
        "title": meta.title if meta else "",
        "hint": (meta.hint if meta else "") or "",
        "fileName": dest.name,
        "sizeBytes": dest.stat().st_size,
        "fileCount": len(files),
        "files": [{"name": p.name, "sizeBytes": p.stat().st_size} for p in files],
    }


def _slot_meta(key: str) -> PlaceholderItem | None:
    for item in DEFAULT_SLOTS:
        if item.key == key:
            return item
    return None


def slot_status(item: PlaceholderItem) -> dict[str, object]:
    key = (item.key or "").strip()
    files: list[Path] = []
    if key and _KEY_RE.match(key):
        files = list_slot_files(key)
    return {
        "key": key,
        "title": item.title,
        "hint": item.hint or "",
        "fileCount": len(files),
        "files": [{"name": p.name, "sizeBytes": p.stat().st_size} for p in files],
    }


def list_slots_status(extra: list[PlaceholderItem] | None = None) -> list[dict[str, object]]:
    return [slot_status(item) for item in collect_slots(extra)]


def library_payload() -> dict[str, object]:
    """投标资料库：仅公司常备扫描件（不含邀请书临时 extras）。"""
    slots = [slot_status(item) for item in DEFAULT_SLOTS]
    filled = sum(1 for s in slots if int(s.get("fileCount") or 0) > 0)
    return {
        "slots": slots,
        "filledCount": filled,
        "totalCount": len(slots),
        "hint": "在此维护公司常备扫描件；生成投标文件时自动引用。本标专用材料可在「投标文件」页临时覆盖。",
    }


def attachments_for_slots(slots: list[PlaceholderItem]) -> dict[str, list[Path]]:
    """生成 Word 时：有文件的 key → 按序文件列表。"""
    out: dict[str, list[Path]] = {}
    for item in slots:
        key = (item.key or "").strip()
        if not key or not _KEY_RE.match(key) or key == TECH_DRAWING_SLOT.key:
            continue
        files = list_slot_files(key)
        if files:
            out[key] = files
    return out


def _invite_id(invitation_id: str) -> str:
    raw = (invitation_id or "").strip()
    if not _DRAWING_ID_RE.fullmatch(raw):
        raise AppError(ErrorCode.VALIDATION, "请先识别邀请书后再上传本项目图纸", status_code=422)
    return raw


def drawing_dir(invitation_id: str) -> Path:
    path = tender_assets_dir() / "drawings" / _invite_id(invitation_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_drawing_files(invitation_id: str) -> list[Path]:
    raw = (invitation_id or "").strip()
    if not _DRAWING_ID_RE.fullmatch(raw):
        return []
    folder = tender_assets_dir() / "drawings" / raw
    if not folder.is_dir():
        return []
    files = [
        p
        for p in folder.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in _ALLOWED_EXT
    ]
    return sorted(files, key=lambda p: p.name.lower())


def attach_invitation_drawings(
    media: dict[str, list[Path]], invitation_id: str
) -> dict[str, list[Path]]:
    key = TECH_DRAWING_SLOT.key
    files = list_drawing_files(invitation_id)
    if files:
        media[key] = files
    else:
        media.pop(key, None)
    return media


def drawing_slot_status(invitation_id: str = "") -> dict[str, object]:
    files = list_drawing_files(invitation_id)
    return {
        "key": TECH_DRAWING_SLOT.key,
        "title": TECH_DRAWING_SLOT.title,
        "hint": TECH_DRAWING_SLOT.hint or "",
        "fileCount": len(files),
        "files": [{"name": p.name, "sizeBytes": p.stat().st_size} for p in files],
    }


def save_drawing_file(
    *,
    invitation_id: str,
    filename: str,
    data: bytes,
    replace: bool = False,
) -> dict[str, object]:
    if not data:
        raise AppError(ErrorCode.VALIDATION, "上传文件为空", status_code=422)
    if len(data) > _MAX_BYTES:
        raise AppError(ErrorCode.VALIDATION, "单个文件不能超过 40MB", status_code=422)
    name = Path((filename or "upload.bin").replace("\\", "/").split("/")[-1]).name
    suffix = Path(name).suffix.lower()
    if suffix not in _ALLOWED_EXT:
        raise AppError(
            ErrorCode.VALIDATION,
            "仅支持 PDF / PNG / JPG / WEBP / GIF / BMP",
            status_code=422,
        )
    folder = drawing_dir(invitation_id)
    if replace:
        for item in list(folder.iterdir()):
            if item.is_file():
                item.unlink(missing_ok=True)
            elif item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
    existing = list_drawing_files(invitation_id)
    if len(existing) >= _MAX_FILES_PER_SLOT:
        raise AppError(ErrorCode.VALIDATION, f"每项最多 {_MAX_FILES_PER_SLOT} 个文件", status_code=422)
    stem = Path(name).stem[:80] or "scan"
    stem = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", stem).strip("_") or "scan"
    dest = folder / f"{stem}{suffix}"
    n = 1
    while dest.exists():
        dest = folder / f"{stem}_{n}{suffix}"
        n += 1
    dest.write_bytes(data)
    payload = drawing_slot_status(invitation_id)
    payload["fileName"] = dest.name
    payload["sizeBytes"] = dest.stat().st_size
    return payload


def clear_drawing_files(invitation_id: str) -> dict[str, object]:
    folder = drawing_dir(invitation_id)
    for item in list(folder.iterdir()):
        if item.is_file():
            item.unlink(missing_ok=True)
        elif item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
    return drawing_slot_status(invitation_id)


def default_slots_payload() -> list[dict[str, object]]:
    return [slot_status(item) for item in DEFAULT_SLOTS]
