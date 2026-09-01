"""规范化模型 API Base：去掉重复协议、去掉已拼好的接口路径。"""

from __future__ import annotations

import re

_SCHEME_RE = re.compile(r"^(https?://)+", re.IGNORECASE)
_TRAILING_CHAT = (
    "/chat/completions",
    "/completions",
    "/embeddings",
    "/images/generations",
)
_NATIVE_AIGC = (
    "/api/v1/services/aigc/multimodal-generation/generation",
    "/api/v1/services/aigc/multimodal-generation",
    "/api/v1/services/aigc/text-generation/generation",
    "/api/v1/services/aigc/text-generation",
    "/api/v1/services/aigc/multimodal-embedding/multimodal-embedding",
)


def normalize_api_base(raw: str) -> str:
    text = (raw or "").strip().strip("\"'")
    if not text:
        return ""
    text = re.sub(r"\s+", "", text)
    match = _SCHEME_RE.match(text)
    scheme = "https"
    if match:
        first = match.group(0).lower()
        scheme = "http" if first.startswith("http://") and "https://" not in first else "https"
        text = text[match.end() :]
    text = text.lstrip("/")
    # 误写成 https://https://host 时，剩余串仍可能以 https:// 开头
    text = _SCHEME_RE.sub("", text).lstrip("/")
    text = text.rstrip("/")
    if not text:
        return ""
    host_path = text
    lower = f"/{host_path.lower()}"
    for suffix in _TRAILING_CHAT + _NATIVE_AIGC:
        if lower.endswith(suffix):
            host_path = host_path[: -len(suffix)].rstrip("/")
            lower = f"/{host_path.lower()}"
            break
    base = f"{scheme}://{host_path}".rstrip("/")
    host = host_path.split("/", 1)[0].lower()
    path = host_path[len(host) :] if "/" in host_path else ""
    if host.endswith(".maas.aliyuncs.com") and not path:
        base = f"{scheme}://{host}/compatible-mode/v1"
    return base
