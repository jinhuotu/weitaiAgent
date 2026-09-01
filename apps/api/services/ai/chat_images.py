"""对话附图：校验、落盘、拼成 OpenAI image_url。"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Any

from common.config import get_settings
from common.errors import AppError, ErrorCode

ALLOWED_MIME = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}
MAX_IMAGES = 4
MAX_BYTES = 4 * 1024 * 1024
DEFAULT_IMAGE_PROMPT = "请根据图片内容作答。"


def _storage_root() -> Path:
    return Path(get_settings().storage_root).expanduser().resolve()


def resolve_chat_path(key: str) -> Path:
    rel = (key or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in Path(rel).parts or not rel.startswith("chat/"):
        raise AppError(ErrorCode.BAD_REQUEST, "invalid image key", status_code=400)
    root = _storage_root()
    path = (root / rel).resolve()
    if not path.is_relative_to(root):
        raise AppError(ErrorCode.BAD_REQUEST, "invalid image key", status_code=400)
    return path


def decode_chat_images(raw: list[dict[str, Any]] | None) -> list[tuple[str, bytes]]:
    items = list(raw or [])
    if len(items) > MAX_IMAGES:
        raise AppError(
            ErrorCode.VALIDATION,
            f"最多上传 {MAX_IMAGES} 张图片",
            status_code=422,
        )
    out: list[tuple[str, bytes]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        mime = str(item.get("mimeType") or item.get("mime_type") or "").strip().lower()
        if mime not in ALLOWED_MIME:
            raise AppError(
                ErrorCode.VALIDATION,
                "图片仅支持 jpeg / png / webp / gif",
                status_code=422,
            )
        data = str(item.get("data") or "").strip()
        if data.startswith("data:") and "," in data:
            data = data.split(",", 1)[1]
        data = "".join(data.split())
        if not data:
            raise AppError(ErrorCode.VALIDATION, "图片数据为空", status_code=422)
        try:
            blob = base64.b64decode(data, validate=False)
        except (binascii.Error, ValueError) as exc:
            raise AppError(ErrorCode.VALIDATION, "图片 Base64 无效", status_code=422) from exc
        if not blob:
            raise AppError(ErrorCode.VALIDATION, "图片数据为空", status_code=422)
        if len(blob) > MAX_BYTES:
            raise AppError(
                ErrorCode.VALIDATION,
                f"单张图片不能超过 {MAX_BYTES // (1024 * 1024)} MB",
                status_code=422,
            )
        out.append(("image/jpeg" if mime == "image/jpg" else mime, blob))
    return out


def persist_chat_images(
    *,
    session_public_id: str,
    msg_id: str,
    blobs: list[tuple[str, bytes]],
) -> list[dict[str, str]]:
    saved: list[dict[str, str]] = []
    for i, (mime, blob) in enumerate(blobs):
        ext = ALLOWED_MIME.get(mime, "jpg")
        key = f"chat/{session_public_id}/{msg_id}_{i}.{ext}"
        path = resolve_chat_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
        saved.append({"fileKey": key, "mimeType": mime})
    return saved


def image_to_data_url(item: dict[str, Any]) -> str | None:
    existing = str(item.get("dataUrl") or item.get("data_url") or "").strip()
    if existing.startswith("data:"):
        return existing
    key = str(item.get("fileKey") or item.get("file_key") or "").strip()
    mime = str(item.get("mimeType") or item.get("mime_type") or "image/jpeg").strip()
    if not key:
        return None
    try:
        path = resolve_chat_path(key)
    except AppError:
        return None
    if not path.is_file():
        return None
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def hydrate_images_for_api(images: Any) -> list[dict[str, str]]:
    if not isinstance(images, list):
        return []
    out: list[dict[str, str]] = []
    for item in images:
        if not isinstance(item, dict):
            continue
        if item.get("__layoutAttachment") or item.get("attachment"):
            continue
        url = image_to_data_url(item)
        mime = str(item.get("mimeType") or item.get("mime_type") or "image/jpeg")
        row: dict[str, str] = {"mimeType": mime}
        if url:
            row["dataUrl"] = url
        key = str(item.get("fileKey") or item.get("file_key") or "").strip()
        if key:
            row["fileKey"] = key
        if url or key:
            out.append(row)
    return out


def extract_layout_attachments(images: Any) -> list[dict[str, Any]]:
    if not isinstance(images, list):
        return []
    out: list[dict[str, Any]] = []
    for item in images:
        if not isinstance(item, dict):
            continue
        if not (item.get("__layoutAttachment") or item.get("attachment")):
            continue
        name = str(item.get("fileName") or "").strip()
        if not name:
            continue
        out.append(
            {
                "fileName": name,
                "kind": str(item.get("kind") or ""),
                "label": str(item.get("label") or name),
            }
        )
    return out


def merge_images_with_attachments(
    images: list[dict[str, Any]] | None,
    attachments: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = list(images or [])
    for a in attachments or []:
        if not isinstance(a, dict) or not a.get("fileName"):
            continue
        merged.append(
            {
                "__layoutAttachment": True,
                "fileName": str(a.get("fileName")),
                "kind": str(a.get("kind") or ""),
                "label": str(a.get("label") or a.get("fileName")),
            }
        )
    return merged


def llm_image_parts(images: Any) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    if not isinstance(images, list):
        return parts
    for item in images:
        if not isinstance(item, dict):
            continue
        url = image_to_data_url(item)
        if not url:
            continue
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def multimodal_user_content(
    text: str,
    blobs: list[tuple[str, bytes]],
) -> list[dict[str, Any]]:
    """用内存中的图片字节直接拼 OpenAI/DashScope 兼容 content，不依赖落盘回读。"""
    caption = (text or "").strip() or DEFAULT_IMAGE_PROMPT
    parts: list[dict[str, Any]] = [{"type": "text", "text": caption}]
    for mime, raw in blobs:
        b64 = base64.b64encode(raw).decode("ascii")
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            }
        )
    return parts


def blobs_to_state_images(blobs: list[tuple[str, bytes]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for mime, raw in blobs:
        b64 = base64.b64encode(raw).decode("ascii")
        out.append({"mimeType": mime, "dataUrl": f"data:{mime};base64,{b64}"})
    return out


def state_images_from_input(raw: Any) -> list[dict[str, str]]:
    """工作流 input.images：支持 data / dataUrl。"""
    if not isinstance(raw, list) or not raw:
        return []
    ready: list[dict[str, str]] = []
    to_decode: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        mime = str(item.get("mimeType") or item.get("mime_type") or "image/jpeg")
        url = str(item.get("dataUrl") or item.get("data_url") or "").strip()
        data = str(item.get("data") or "").strip()
        if url.startswith("data:"):
            ready.append({"mimeType": mime, "dataUrl": url})
        elif data:
            to_decode.append(item)
        elif url:
            ready.append({"mimeType": mime, "dataUrl": url})
    if to_decode:
        ready.extend(blobs_to_state_images(decode_chat_images(to_decode)))
    if len(ready) > MAX_IMAGES:
        raise AppError(
            ErrorCode.VALIDATION,
            f"最多上传 {MAX_IMAGES} 张图片",
            status_code=422,
        )
    return ready


def data_url_to_blob(item: dict[str, Any]) -> tuple[str, bytes] | None:
    url = image_to_data_url(item)
    if not url or not url.startswith("data:") or "," not in url:
        return None
    header, b64 = url.split(",", 1)
    mime = "image/jpeg"
    if ";base64" in header:
        mime = header[5:].split(";", 1)[0].strip() or mime
    if mime not in ALLOWED_MIME:
        mime = "image/jpeg"
    data = "".join(b64.split())
    try:
        blob = base64.b64decode(data, validate=False)
    except (binascii.Error, ValueError):
        return None
    if not blob or len(blob) > MAX_BYTES:
        return None
    return ("image/jpeg" if mime == "image/jpg" else mime, blob)


def output_images_to_blobs(images: Any) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    if not isinstance(images, list):
        return out
    for item in images:
        if not isinstance(item, dict):
            continue
        parsed = data_url_to_blob(item)
        if parsed:
            out.append(parsed)
        if len(out) >= MAX_IMAGES:
            break
    return out


_IMAGE_SUFFIX_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def read_local_image(
    path_str: str,
    *,
    allow_paths: list[str] | None = None,
) -> tuple[str, bytes]:
    """读取本地图片。仅允许 storage 根目录，或与 allow_paths 同文件/同目录。"""
    raw_path = (path_str or "").strip().strip('"').strip("'")
    if not raw_path:
        raise AppError(ErrorCode.VALIDATION, "图片路径为空", status_code=422)
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise AppError(ErrorCode.VALIDATION, f"图片文件不存在：{path}", status_code=422)
    mime = _IMAGE_SUFFIX_MIME.get(path.suffix.lower())
    if not mime:
        raise AppError(
            ErrorCode.VALIDATION,
            "仅支持读取 png / jpeg / webp / gif",
            status_code=422,
        )
    allowed = False
    root = _storage_root()
    try:
        if path.is_relative_to(root):
            allowed = True
    except (OSError, ValueError):
        allowed = False
    for ap in allow_paths or []:
        other_raw = str(ap or "").strip()
        if not other_raw:
            continue
        try:
            other = Path(other_raw).expanduser().resolve()
        except (OSError, ValueError):
            continue
        if path == other or (other.exists() and path.parent == other.parent):
            allowed = True
            break
    if not allowed:
        raise AppError(
            ErrorCode.VALIDATION,
            "不允许读取该路径（须在存储目录或本次工具保存目录内）",
            status_code=422,
        )
    blob = path.read_bytes()
    if not blob:
        raise AppError(ErrorCode.VALIDATION, "图片文件为空", status_code=422)
    if len(blob) > MAX_BYTES:
        raise AppError(
            ErrorCode.VALIDATION,
            f"单张图片不能超过 {MAX_BYTES // (1024 * 1024)} MB",
            status_code=422,
        )
    return mime, blob
