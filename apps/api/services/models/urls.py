"""规范化模型 API Base：去掉重复协议、去掉已拼好的接口路径。"""

from __future__ import annotations

import re

_SCHEME_RE = re.compile(r"^(https?://)+", re.IGNORECASE)


def host_is_private_or_local(host: str) -> bool:
    """本机 / 内网地址：无协议时默认 http，避免局域网被补成 https。"""
    name = (host or "").split(":")[0].strip().lower().strip("[]")
    if name in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        return True
    parts = name.split(".")
    if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        a, b = int(parts[0]), int(parts[1])
        if a in {10, 127}:
            return True
        if a == 192 and b == 168:
            return True
        if a == 172 and 16 <= b <= 31:
            return True
    return False


def is_loopback_api_base(api_base: str) -> bool:
    host = (api_base or "").split("://", 1)[-1].split("/", 1)[0]
    name = host.split(":")[0].strip().lower().strip("[]")
    return name in {"127.0.0.1", "localhost", "::1"}


def _fix_host_port(host_path: str) -> str:
    """把 http://192.168.x.x/:8001 这种误写改成 host:port。"""
    text = host_path or ""
    text = re.sub(r"^([^/]+)/+:(\d+)", r"\1:\2", text)
    text = re.sub(r"^([^/:]+):/+(\d+)", r"\1:\2", text)
    return text


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
    if not match and host_is_private_or_local(text.split("/", 1)[0]):
        scheme = "http"
    # 误写成 https://https://host 时，剩余串仍可能以 https:// 开头
    text = _SCHEME_RE.sub("", text).lstrip("/")
    text = text.rstrip("/")
    if not text:
        return ""
    host_path = _fix_host_port(text)
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
