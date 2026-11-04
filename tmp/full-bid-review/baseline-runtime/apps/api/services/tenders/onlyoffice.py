"""OnlyOffice Document Server：在线 Word 编辑配置与保存回调。"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import httpx
from jose import JWTError, jwt

from api.services.tenders.generate import resolve_output_file
from common.config import Settings, get_settings
from common.errors import AppError, ErrorCode

logger = logging.getLogger("api.tenders.onlyoffice")

# 预览模板打开的是标书输出目录里的副本。生成读的是邀请书目录那份。
_FORMAT_NAME_RE = re.compile(r"^[a-f0-9]{10,16}\.format\.docx$", re.I)


def onlyoffice_enabled() -> bool:
    return bool(get_settings().onlyoffice_document_server_url.strip())


def _jwt_secret(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    secret = (settings.onlyoffice_jwt_secret or "").strip()
    if secret:
        return secret
    return settings.jwt_secret_key


def document_key(path: Path) -> str:
    stat = path.stat()
    raw = f"{path.name}-{stat.st_mtime_ns}-{stat.st_size}"
    # 后缀用于作废 OnlyOffice 对「转换失败」的缓存；失败后同一 key 会一直弹「下载失败」。
    return hashlib.sha256(raw.encode()).hexdigest()[:32] + "-g4"


def create_download_token(file_name: str, *, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    expire = datetime.now(UTC) + timedelta(seconds=settings.onlyoffice_download_token_ttl_seconds)
    payload = {"type": "oo_download", "file": file_name, "exp": expire}
    return jwt.encode(payload, _jwt_secret(settings), algorithm="HS256")


def verify_download_token(token: str, file_name: str, *, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    try:
        payload = jwt.decode(token, _jwt_secret(settings), algorithms=["HS256"])
    except JWTError:
        return False
    return payload.get("type") == "oo_download" and payload.get("file") == file_name


def _sign_config(config: dict, *, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    secret = (settings.onlyoffice_jwt_secret or "").strip()
    if not secret:
        return config
    signed = dict(config)
    signed["token"] = jwt.encode(config, secret, algorithm="HS256")
    return signed


def _decode_callback_body(body: dict, *, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    secret = (settings.onlyoffice_jwt_secret or "").strip()
    token = body.get("token")
    if secret and token:
        try:
            decoded = jwt.decode(str(token), secret, algorithms=["HS256"])
            if isinstance(decoded, dict):
                return decoded
        except JWTError:
            logger.warning("onlyoffice callback jwt invalid")
            raise ValueError("invalid callback token")
    return body


def build_editor_config(
    file_name: str,
    *,
    download_name: str,
    user_id: str,
    user_name: str,
    editor_height_px: int | None = None,
    mode: str = "edit",
    settings: Settings | None = None,
) -> dict:
    settings = settings or get_settings()
    path = resolve_output_file(file_name)
    api_base = settings.onlyoffice_public_api_base.rstrip("/")
    prefix = settings.api_prefix.rstrip("/")

    dl_token = create_download_token(file_name, settings=settings)
    doc_url = (
        f"{api_base}{prefix}/tenders/files/{quote(file_name)}/onlyoffice-download"
        f"?token={quote(dl_token, safe='')}"
    )
    callback_url = (
        f"{api_base}{prefix}/tenders/files/{quote(file_name)}/onlyoffice-callback"
    )

    # editor_height_px 仍由接口传入，高度改由前端容器 100% 撑满，便于全屏拉伸。
    _ = editor_height_px
    view_mode = (mode or "edit").strip().lower() == "view"

    config = {
        "documentType": "word",
        "document": {
            "fileType": "docx",
            "key": document_key(path) + ("-v" if view_mode else ""),
            "title": download_name or file_name,
            "url": doc_url,
            "permissions": {
                "edit": not view_mode,
                "download": True,
                "print": True,
                "review": False,
                "comment": False,
            },
        },
        "editorConfig": {
            "callbackUrl": callback_url,
            "lang": "zh-CN",
            "region": "zh-CN",
            "mode": "view" if view_mode else "edit",
            "user": {
                "id": user_id or "user",
                "name": user_name or "用户",
            },
            "customization": {
                "forcesave": not view_mode,
                "autosave": not view_mode,
                "chat": False,
                "comments": False,
                "help": False,
                "hideRightMenu": True,
                "spellcheck": False,
                "compatibleFeatures": True,
                "forceWesternFontSize": False,
                "unit": "cm",
                "features": {
                    "spellcheck": False,
                },
                "zoom": -2,
            },
        },
        "height": "100%",
        "width": "100%",
        "type": "desktop",
    }
    return _sign_config(config, settings=settings)


async def force_save_document(
    file_name: str, key: str, *, settings: Settings | None = None
) -> None:
    """Ask Document Server to save the open editor; its status-6 callback writes the file."""
    settings = settings or get_settings()
    path = resolve_output_file(file_name)
    if key != document_key(path):
        raise AppError(ErrorCode.BAD_REQUEST, "模板版本已变化，请重新打开预览", status_code=409)
    command = {"c": "forcesave", "key": key}
    secret = (settings.onlyoffice_jwt_secret or "").strip()
    payload = (
        {**command, "token": jwt.encode(command, secret, algorithm="HS256")} if secret else command
    )
    url = f"{settings.onlyoffice_document_server_url.rstrip('/')}/command"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, params={"shardkey": key}, json=payload)
            response.raise_for_status()
            result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.exception("onlyoffice forcesave command failed file=%s", file_name)
        raise AppError(
            ErrorCode.INTERNAL, "OnlyOffice 保存请求失败，请稍后重试", status_code=502
        ) from exc
    error = result.get("error") if isinstance(result, dict) else None
    if error != 0:
        logger.warning("onlyoffice forcesave rejected file=%s error=%s", file_name, error)
        message = (
            "编辑器尚未收到修改，请稍后重试"
            if error == 4
            else "OnlyOffice 拒绝保存请求，请检查服务与 JWT"
        )
        raise AppError(ErrorCode.INTERNAL, message, status_code=409 if error == 4 else 502)


async def handle_callback(file_name: str, body: dict) -> dict[str, int]:
    """OnlyOffice 保存回调。成功须返回 ``{"error": 0}``。"""
    try:
        payload = _decode_callback_body(body)
    except ValueError:
        return {"error": 1}

    status = payload.get("status")
    if status not in (2, 6):
        return {"error": 0}

    url = payload.get("url")
    if not url:
        logger.warning("onlyoffice callback missing url file=%s status=%s", file_name, status)
        return {"error": 1}

    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            resp = await client.get(str(url))
            resp.raise_for_status()
            data = resp.content
    except Exception:
        logger.exception("onlyoffice fetch edited document failed file=%s", file_name)
        return {"error": 1}

    path = resolve_output_file(file_name)
    path.write_bytes(data)
    _keep_format_template(file_name, path)
    logger.info(
        "onlyoffice saved file=%s bytes=%s status=%s key=%s",
        file_name,
        len(data),
        status,
        payload.get("key"),
    )
    return {"error": 0}


def _keep_format_template(file_name: str, src: Path) -> None:
    """格式章改稿写回邀请书目录，下次预览和按本标生成都用这份。"""
    name = Path(file_name).name
    if not _FORMAT_NAME_RE.fullmatch(name):
        return
    from api.services.tenders.pdf_format import format_docx_path

    dest = format_docx_path(name.split(".", 1)[0])
    if dest is None:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.resolve() == src.resolve():
        return
    shutil.copyfile(src, dest)
    logger.info("onlyoffice format template synced file=%s bytes=%s", dest.name, src.stat().st_size)


def confirm_format_template(invitation_id: str) -> str:
    """Commit the saved preview copy and return its key for confirmation."""
    from api.services.tenders.pdf_format import format_docx_path

    dest = format_docx_path(invitation_id)
    if dest is None:
        raise ValueError("无效的招标文件标识")
    src = resolve_output_file(dest.name)
    if not src.is_file() or src.stat().st_size < 1000:
        raise ValueError("格式章预览文件不存在，请重新预览")
    _keep_format_template(dest.name, src)
    if not dest.is_file() or dest.read_bytes() != src.read_bytes():
        raise OSError("格式章模板写回失败")
    return document_key(src)
