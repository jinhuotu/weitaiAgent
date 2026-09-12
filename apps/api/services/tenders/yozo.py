"""永中 Web Office 内网私有化：远程文档开档 + 保存回调。

私有化 3.x 不使用 OnlyOffice 的 DocsAPI。浏览器用 iframe 打开永中页面，
永中再拉取本系统的 docx；保存/关档时把文件流 POST 回 callbackUrl。
开档参数名以永中测试包为准，可用 YOZO_OPEN_PATH / YOZO_OPEN_STYLE 微调。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from urllib.parse import quote, urlencode

import httpx
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.requests import Request

from api.services.tenders.generate import resolve_output_file
from api.services.tenders.onlyoffice import create_download_token
from common.config import Settings, get_settings

logger = logging.getLogger("api.tenders.yozo")

_DOCX_MAGIC = b"PK"


def yozo_enabled(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return bool(settings.yozo_document_server_url.strip())


def generate_sign(secret: str, params: dict[str, list[str] | str]) -> str:
    """永中云编辑 HMAC-SHA256 签名（官方 Python SDK 同款）。"""
    normalized: dict[str, list[str]] = {}
    for key, value in params.items():
        if key == "sign":
            continue
        if isinstance(value, list):
            normalized[key] = [str(v) for v in value]
        else:
            normalized[key] = [str(value)]
    chunks: list[str] = []
    for key in sorted(normalized):
        values = sorted(normalized[key])
        if values:
            for item in values:
                chunks.append(f"{key}={item}")
        else:
            chunks.append(f"{key}=")
    raw = "".join(chunks)
    digest = hmac.new(secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).digest()
    return digest.hex().upper()


def _public_api_base(settings: Settings) -> str:
    return (
        settings.yozo_public_api_base.strip()
        or settings.onlyoffice_public_api_base.strip()
        or "http://127.0.0.1:8100"
    ).rstrip("/")


def _open_params(
    file_name: str,
    *,
    download_name: str,
    user_id: str,
    user_name: str,
    mode: str,
    settings: Settings,
) -> dict[str, str]:
    prefix = settings.api_prefix.rstrip("/")
    api_base = _public_api_base(settings)
    token = create_download_token(file_name, settings=settings)
    file_url = (
        f"{api_base}{prefix}/tenders/files/{quote(file_name)}/yozo-download"
        f"?token={quote(token, safe='')}"
    )
    callback_url = f"{api_base}{prefix}/tenders/files/{quote(file_name)}/yozo-callback"
    view_mode = (mode or "edit").strip().lower() == "view"
    params = {
        "fileId": file_name,
        "fileName": download_name or file_name,
        "fileUrl": file_url,
        "callbackUrl": callback_url,
        "userId": user_id or "user",
        "userName": user_name or "用户",
        "userRight": "0" if view_mode else "1",
    }
    app_id = (settings.yozo_app_id or "").strip()
    app_key = (settings.yozo_app_key or "").strip()
    if app_id:
        params["appId"] = app_id
        if app_key:
            sign_map = {key: [value] for key, value in params.items()}
            params["sign"] = generate_sign(app_key, sign_map)
    return params


def build_iframe_url(
    file_name: str,
    *,
    download_name: str,
    user_id: str,
    user_name: str,
    mode: str = "edit",
    settings: Settings | None = None,
) -> str:
    settings = settings or get_settings()
    resolve_output_file(file_name)
    server = settings.yozo_document_server_url.strip().rstrip("/")
    path = (settings.yozo_open_path or "/").strip() or "/"
    if not path.startswith("/"):
        path = "/" + path
    params = _open_params(
        file_name,
        download_name=download_name,
        user_id=user_id,
        user_name=user_name,
        mode=mode,
        settings=settings,
    )
    style = (settings.yozo_open_style or "query").strip().lower()
    if style == "json":
        query = urlencode({"jsonParams": json.dumps(params, ensure_ascii=False)})
    else:
        query = urlencode(params)
    return f"{server}{path}?{query}"


def build_editor_payload(
    file_name: str,
    *,
    download_name: str,
    user_id: str,
    user_name: str,
    mode: str = "edit",
    settings: Settings | None = None,
) -> dict[str, object]:
    settings = settings or get_settings()
    iframe_url = build_iframe_url(
        file_name,
        download_name=download_name,
        user_id=user_id,
        user_name=user_name,
        mode=mode,
        settings=settings,
    )
    params = _open_params(
        file_name,
        download_name=download_name,
        user_id=user_id,
        user_name=user_name,
        mode=mode,
        settings=settings,
    )
    return {
        "engine": "yozo",
        "documentServerUrl": settings.yozo_document_server_url.strip().rstrip("/"),
        "iframeUrl": iframe_url,
        "config": params,
    }


def _ok() -> dict[str, object]:
    return {"error": 0, "errorCode": "0", "errorMessage": "ok"}


def _fail(message: str = "save failed") -> dict[str, object]:
    return {"error": 1, "errorCode": "1", "errorMessage": message}


def _write_docx(file_name: str, data: bytes) -> None:
    if not data or len(data) < 4:
        raise ValueError("empty document")
    path = resolve_output_file(file_name)
    path.write_bytes(data)
    logger.info("yozo saved file=%s bytes=%s", file_name, len(data))


async def _download_version(file_version_id: str, settings: Settings) -> bytes:
    base = (settings.yozo_api_base or "").strip().rstrip("/")
    app_id = (settings.yozo_app_id or "").strip()
    app_key = (settings.yozo_app_key or "").strip()
    if not base or not app_id or not app_key:
        raise ValueError("yozo download not configured")
    params = {"appId": [app_id], "fileVersionId": [file_version_id]}
    sign = generate_sign(app_key, params)
    url = f"{base}/api/file/download?appId={quote(app_id)}&fileVersionId={quote(file_version_id)}&sign={quote(sign)}"
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


async def _bytes_from_form(form) -> bytes | None:
    for key in ("file", "content", "document", "uploadFile"):
        item = form.get(key)
        if item is None:
            continue
        if isinstance(item, (StarletteUploadFile,)):
            data = await item.read()
            if data:
                return data
        if hasattr(item, "read") and callable(item.read):
            data = item.read()
            if hasattr(data, "__await__"):
                data = await data
            if isinstance(data, bytes) and data:
                return data
    return None


async def handle_callback(file_name: str, request: Request) -> dict[str, object]:
    """永中保存回调。兼容 multipart 文件流、原始 docx、JSON url / newFileId。"""
    settings = get_settings()
    ctype = (request.headers.get("content-type") or "").lower()
    try:
        if "multipart/form-data" in ctype or "application/x-www-form-urlencoded" in ctype:
            form = await request.form()
            data = await _bytes_from_form(form)
            if data:
                _write_docx(file_name, data)
                return _ok()
            version = str(form.get("newFileId") or form.get("fileVersionId") or "").strip()
            if version:
                payload = await _download_version(version, settings)
                _write_docx(file_name, payload)
                return _ok()
            logger.info("yozo callback form without file file=%s", file_name)
            return _ok()

        raw = await request.body()
        if not raw:
            return _ok()
        if raw[:2] == _DOCX_MAGIC:
            _write_docx(file_name, raw)
            return _ok()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("yozo callback unknown body file=%s ctype=%s", file_name, ctype)
            return _fail("invalid callback body")
        if not isinstance(payload, dict):
            return _fail("invalid callback body")
        error_code = str(payload.get("errorCode") or payload.get("error") or "0")
        if error_code not in {"0", "0.0"}:
            logger.warning("yozo callback error file=%s payload=%s", file_name, payload)
            return _ok()
        url = payload.get("url") or payload.get("fileUrl")
        if url:
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                resp = await client.get(str(url))
                resp.raise_for_status()
                _write_docx(file_name, resp.content)
            return _ok()
        version = str(payload.get("newFileId") or payload.get("fileVersionId") or "").strip()
        if version:
            downloaded = await _download_version(version, settings)
            _write_docx(file_name, downloaded)
            return _ok()
        logger.info("yozo callback ack file=%s keys=%s", file_name, list(payload.keys()))
        return _ok()
    except Exception:
        logger.exception("yozo callback failed file=%s", file_name)
        return _fail("save failed")
