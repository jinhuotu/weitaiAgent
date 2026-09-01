"""OnlyOffice Document Server：在线 Word 编辑配置与保存回调。"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import httpx
from jose import JWTError, jwt

from api.services.tenders.generate import resolve_output_file
from common.config import Settings, get_settings

logger = logging.getLogger("api.tenders.onlyoffice")


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
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


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

    config = {
        "documentType": "word",
        "document": {
            "fileType": "docx",
            "key": document_key(path),
            "title": download_name or file_name,
            "url": doc_url,
            "permissions": {
                "edit": True,
                "download": True,
                "print": True,
                "review": False,
            },
        },
        "editorConfig": {
            "callbackUrl": callback_url,
            "lang": "zh-CN",
            "region": "zh-CN",
            "mode": "edit",
            "user": {
                "id": user_id or "user",
                "name": user_name or "用户",
            },
            "customization": {
                "forcesave": True,
                "autosave": True,
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
    }
    return _sign_config(config, settings=settings)


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
    logger.info(
        "onlyoffice saved file=%s bytes=%s status=%s key=%s",
        file_name,
        len(data),
        status,
        payload.get("key"),
    )
    return {"error": 0}
