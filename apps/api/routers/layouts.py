"""布置图纸文件下载（SVG / DXF）。"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from api.deps import CurrentUser, DbSession
from common.config import get_settings
from common.errors import AppError, ErrorCode

router = APIRouter(prefix="/layouts", tags=["layouts"])

_FILE_RE = re.compile(r"^[\w.\u4e00-\u9fff()\-]{1,96}\.(svg|dxf|dwg)$", re.I)
_HASH_TAIL = re.compile(r"-[a-f0-9]{8}(\.(svg|dxf|dwg))$", re.I)
_MIME = {
    "svg": "image/svg+xml",
    "dxf": "application/dxf",
    "dwg": "application/acad",
}


def _layouts_root() -> Path:
    return Path(get_settings().storage_root).expanduser().resolve() / "layouts"


def _download_as(name: str) -> str:
    return _HASH_TAIL.sub(r"\1", name)


@router.get("/files/{file_name}")
async def download_layout_file(
    file_name: str,
    db: DbSession,
    user: CurrentUser,
):
    del db, user
    name = (file_name or "").strip().replace("\\", "/").split("/")[-1]
    if not _FILE_RE.match(name) or ".." in name:
        raise AppError(ErrorCode.BAD_REQUEST, "invalid layout file name", status_code=400)
    root = _layouts_root()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise AppError(ErrorCode.NOT_FOUND, "layout file not found", status_code=404)
    ext = path.suffix.lstrip(".").lower()
    return FileResponse(
        path,
        media_type=_MIME.get(ext, "application/octet-stream"),
        filename=_download_as(name),
    )
