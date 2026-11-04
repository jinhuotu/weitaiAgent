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


_JWT_HINT = (
    "OnlyOffice JWT 校验失败：请将后端 ONLYOFFICE_JWT_SECRET 与文档服务的 JWT_SECRET "
    "设为一致，并检查 JWT_ENABLED 配置；修改后重建 onlyoffice 容器并重启后端"
)


def _server_url(settings: Settings) -> str:
    url = settings.onlyoffice_document_server_url.strip().rstrip("/")
    try:
        parsed = httpx.URL(url)
        valid = parsed.scheme in {"http", "https"} and bool(parsed.host)
    except httpx.InvalidURL:
        valid = False
    if not valid:
        raise AppError(
            ErrorCode.INTERNAL,
            "OnlyOffice 服务地址未配置或无效，请检查 ONLYOFFICE_DOCUMENT_SERVER_URL",
            status_code=503,
        )
    return url


def _signed_command(command: dict, settings: Settings) -> dict:
    secret = (settings.onlyoffice_jwt_secret or "").strip()
    return (
        {**command, "token": jwt.encode(command, secret, algorithm="HS256")} if secret else command
    )


def _transport_error(exc: httpx.HTTPError, settings: Settings, operation: str) -> AppError:
    address = httpx.URL(_server_url(settings))
    port = address.port or (443 if address.scheme == "https" else 80)
    # Log the operation and destination, never the signed payload or URL credentials.
    logger.warning(
        "onlyoffice %s transport=%s host=%s port=%s",
        operation,
        type(exc).__name__,
        address.host,
        port,
    )
    if isinstance(exc, httpx.ConnectError):
        return AppError(
            ErrorCode.INTERNAL,
            f"连不上 OnlyOffice 文档服务（{address.host}:{port}），"
            "请先在 infra 目录执行 docker compose up -d onlyoffice；"
            "若服务已启动，请核对 ONLYOFFICE_DOCUMENT_SERVER_URL、端口和防火墙",
            status_code=503,
        )
    if isinstance(exc, httpx.TimeoutException):
        return AppError(
            ErrorCode.INTERNAL,
            "OnlyOffice 文档服务响应超时，请检查服务负载及网络连接，保留当前编辑窗口后重试",
            status_code=504,
        )
    return AppError(
        ErrorCode.INTERNAL,
        f"OnlyOffice 文档服务通信异常（{type(exc).__name__}），"
        "请检查 ONLYOFFICE_DOCUMENT_SERVER_URL、代理及网络连接",
        status_code=502,
    )


def _log_server_response(response: httpx.Response, settings: Settings, operation: str) -> None:
    body = response.text
    for secret in (settings.onlyoffice_jwt_secret, settings.jwt_secret_key):
        if secret:
            body = body.replace(secret, "[REDACTED]")
    body = re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[JWT REDACTED]", body)
    logger.warning("onlyoffice %s HTTP=%s body=%r", operation, response.status_code, body[:4096])


def _check_http_response(response: httpx.Response, settings: Settings, operation: str) -> None:
    if not 200 <= response.status_code < 300:
        _log_server_response(response, settings, operation)
        if response.status_code in {401, 403}:
            message = (
                f"OnlyOffice 文档服务拒绝访问（HTTP {response.status_code}），"
                "请检查 ONLYOFFICE_JWT_SECRET 与 JWT_SECRET 是否一致，以及反向代理的鉴权配置"
            )
        else:
            message = (
                f"OnlyOffice 文档服务返回 HTTP {response.status_code}，"
                "请检查服务状态、ONLYOFFICE_DOCUMENT_SERVER_URL 和反向代理；返回体已记录到后端日志"
            )
        raise AppError(ErrorCode.INTERNAL, message, status_code=502)


def _command_result(response: httpx.Response, settings: Settings, operation: str) -> dict:
    _check_http_response(response, settings, operation)
    try:
        result = response.json()
    except ValueError as exc:
        _log_server_response(response, settings, operation)
        raise AppError(
            ErrorCode.INTERNAL,
            "OnlyOffice 文档服务返回非 JSON 响应，请检查服务地址和反向代理；返回体已记录到后端日志",
            status_code=502,
        ) from exc
    if not isinstance(result, dict) or type(result.get("error")) is not int:
        _log_server_response(response, settings, operation)
        raise AppError(
            ErrorCode.INTERNAL,
            "OnlyOffice 文档服务响应格式异常，请检查服务地址；返回体已记录到后端日志",
            status_code=502,
        )
    error = result["error"]
    if error == 0:
        return result
    _log_server_response(response, settings, operation)
    if error == 6:
        message, status = _JWT_HINT, 502
    elif error == 4:
        message, status = "编辑器尚未收到修改，请稍候再次保存，保留当前编辑窗口", 409
    elif error == 1:
        message, status = "OnlyOffice 编辑会话不存在或已过期，请重新打开文档后保存", 409
    elif error == 2:
        message, status = (
            "OnlyOffice 保存回调地址无效，请检查 ONLYOFFICE_PUBLIC_API_BASE，"
            "确保文档服务容器能够访问后端 API",
            502,
        )
    elif error == 3:
        message, status = "OnlyOffice 文档服务内部错误，请检查容器日志及服务负载", 502
    elif error == 5:
        message, status = "OnlyOffice 不支持当前命令，请检查 Document Server 版本和服务地址", 502
    else:
        message, status = f"OnlyOffice 拒绝请求（错误码 {error}），请查看后端和文档服务日志", 502
    raise AppError(ErrorCode.INTERNAL, message, status_code=status)


def check_document_server(*, settings: Settings | None = None) -> dict:
    """Probe health and a read-only signed command; never open or change a document."""
    settings = settings or get_settings()
    url = _server_url(settings)
    try:
        # Windows httpx also inherits the system proxy from the registry. Keep
        # document-service traffic direct so local 8082 requests are not proxied.
        with httpx.Client(timeout=httpx.Timeout(5.0, connect=3.0), trust_env=False) as client:
            health = client.get(f"{url}/healthcheck")
            _check_http_response(health, settings, "healthcheck")
            if health.text.strip().lower() != "true":
                _log_server_response(health, settings, "healthcheck")
                raise AppError(
                    ErrorCode.INTERNAL,
                    "OnlyOffice 健康检查未通过，请检查容器服务状态和 "
                    "ONLYOFFICE_DOCUMENT_SERVER_URL",
                    status_code=503,
                )
            response = client.post(
                f"{url}/command", json=_signed_command({"c": "version"}, settings)
            )
            result = _command_result(response, settings, "version")
    except httpx.HTTPError as exc:
        raise _transport_error(exc, settings, "preflight") from exc
    if not isinstance(result.get("version"), str) or not result["version"].strip():
        _log_server_response(response, settings, "version")
        raise AppError(
            ErrorCode.INTERNAL,
            "OnlyOffice 版本响应异常，请检查文档服务地址和版本",
            status_code=502,
        )
    return {
        "reachable": True,
        "healthy": True,
        "commandAccepted": True,
        "jwtConfigured": bool(settings.onlyoffice_jwt_secret.strip()),
        "version": result["version"],
    }


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
        except JWTError as exc:
            logger.warning("onlyoffice callback jwt invalid")
            raise ValueError("invalid callback token") from exc
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
    check_document_server(settings=settings)
    api_base = settings.onlyoffice_public_api_base.rstrip("/")
    prefix = settings.api_prefix.rstrip("/")

    dl_token = create_download_token(file_name, settings=settings)
    doc_url = (
        f"{api_base}{prefix}/tenders/files/{quote(file_name)}/onlyoffice-download"
        f"?token={quote(dl_token, safe='')}"
    )
    callback_url = f"{api_base}{prefix}/tenders/files/{quote(file_name)}/onlyoffice-callback"

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
    payload = _signed_command(command, settings)
    url = f"{_server_url(settings)}/command"
    try:
        async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
            response = await client.post(url, params={"shardkey": key}, json=payload)
    except httpx.HTTPError as exc:
        raise _transport_error(exc, settings, "forcesave") from exc
    _command_result(response, settings, "forcesave")


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
        async with httpx.AsyncClient(
            timeout=120.0,
            follow_redirects=True,
            trust_env=False,
        ) as client:
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
