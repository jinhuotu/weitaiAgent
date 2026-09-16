"""读场地规划图，抽出充电桩/配套设备工程量。"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.services.ai.chat_images import multimodal_user_content
from api.services.knowledge.drawings import preview_jpeg_from_file
from api.services.models.runtime import build_llm_client
from common.errors import AppError, ErrorCode

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_MAX_UPLOAD = 20 * 1024 * 1024
_ALLOWED = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".bmp",
    ".pdf",
}

_PROMPT = """你是充电站造价助理。根据场地规划图提取工程量，只输出 JSON，不要Markdown说明。
规则：
- 桩型 code 只能是 dc_320kw / dc_160kw / dc_120kw / ac_14kw
- 设备 code 可用 box_transformer / ring_cabinet / lv_cabinet / group_host / fire_hydrant
- 图上看不清的数量不要编，放进 uncertainties
- 不要填写单价
JSON 形状：
{
  "projectName": "项目名或空",
  "chargers": [{"code":"dc_160kw","name":"160kW直流充电桩","qty":8,"note":"图例/车位"}],
  "equipment": [{"code":"box_transformer","name":"箱变","qty":1,"specHint":"","note":""}],
  "extras": [{"name":"雨棚","qty":1,"unit":"项","note":"图上有雨棚"}],
  "uncertainties": ["……"]
}
"""


def sniff_ext(filename: str, content_type: str | None) -> str:
    name = (filename or "").lower()
    for ext in _ALLOWED:
        if name.endswith(ext):
            return ext
    mime = (content_type or "").lower()
    if "pdf" in mime:
        return ".pdf"
    if "png" in mime:
        return ".png"
    if "jpeg" in mime or "jpg" in mime:
        return ".jpg"
    if "webp" in mime:
        return ".webp"
    return ""


def rasterize_upload(filename: str, content_type: str | None, blob: bytes) -> tuple[str, bytes]:
    if not blob:
        raise AppError(ErrorCode.VALIDATION, "图片为空", status_code=422)
    if len(blob) > _MAX_UPLOAD:
        raise AppError(ErrorCode.VALIDATION, "单张规划图不能超过 20MB", status_code=422)
    ext = sniff_ext(filename, content_type)
    if ext not in _ALLOWED:
        raise AppError(ErrorCode.VALIDATION, "规划图请上传图片或 PDF", status_code=422)
    suffix = ext or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
        fh.write(blob)
        tmp = Path(fh.name)
    try:
        jpeg = preview_jpeg_from_file(tmp, ext=ext.lstrip("."), max_px=1600)
    finally:
        tmp.unlink(missing_ok=True)
    if not jpeg:
        raise AppError(ErrorCode.VALIDATION, "无法读取规划图", status_code=422)
    return "image/jpeg", jpeg


def extract_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise AppError(ErrorCode.INTERNAL, "读图结果为空", status_code=502)
    m = _FENCE_RE.search(raw)
    snippet = m.group(1).strip() if m else raw
    start = snippet.find("{")
    if start < 0:
        raise AppError(ErrorCode.INTERNAL, "读图未返回 JSON", status_code=502)
    snippet = snippet[start:]
    depth = 0
    end = -1
    in_str = False
    esc = False
    for i, ch in enumerate(snippet):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        raise AppError(ErrorCode.INTERNAL, "读图 JSON 不完整", status_code=502)
    try:
        data = json.loads(snippet[: end + 1])
    except json.JSONDecodeError as exc:
        raise AppError(ErrorCode.INTERNAL, "读图 JSON 无法解析", status_code=502) from exc
    if not isinstance(data, dict):
        raise AppError(ErrorCode.INTERNAL, "读图 JSON 格式不对", status_code=502)
    return data


async def read_site_map(
    db: AsyncSession,
    blobs: list[tuple[str, bytes]],
    *,
    note: str = "",
) -> dict[str, Any]:
    if not blobs:
        raise AppError(ErrorCode.VALIDATION, "请上传场地规划图", status_code=422)
    client = await build_llm_client(db, "fast", require_vision=True)
    user = _PROMPT
    extra = (note or "").strip()
    if extra:
        user += f"\n用户补充：{extra}"
    text = await client.complete(
        [{"role": "user", "content": multimodal_user_content(user, blobs)}],
        mode="fast",
    )
    return extract_json(text)
