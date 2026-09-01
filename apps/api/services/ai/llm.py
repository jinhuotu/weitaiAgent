"""OpenAI 兼容 Chat Completions 客户端。

配置只来自「模型管理」表，不读 .env 的 LLM_*。
支持标准 tool_calls，以及豆包等模型把工具调用写成 DSML 正文的情况。
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from api.services.ai.tool_call_text import (
    looks_like_dsml,
    normalize_dsml_delimiters,
    parse_tool_calls_from_content,
    strip_tool_call_markup,
)
from api.services.models.urls import normalize_api_base
from common.errors import AppError, ErrorCode
from common.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_MAX_TOKENS = 8192


def _choice_piece_text(choice: dict[str, Any]) -> tuple[str, str]:
    """流式/非流式 chunk：正文 vs 思考链。DeepSeek-R1 常把答案放在 reasoning_content。"""
    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content = ""
    reason = ""
    for src in (delta, message):
        raw = src.get("content")
        if raw is None:
            raw = src.get("text")
        if isinstance(raw, str) and raw:
            content += raw
        for key in ("reasoning_content", "reasoning"):
            piece = src.get(key)
            if isinstance(piece, str) and piece:
                reason += piece
    return content, reason


def _http_error_message(exc: BaseException) -> str:
    detail = (str(exc) or "").strip() or type(exc).__name__
    lowered = detail.lower()
    if "getaddrinfo" in lowered or "name or service not known" in lowered:
        return (
            "无法解析 API 地址（DNS 失败）。请检查「模型管理」里的 API Base："
            "不要写成 https://https://，应填 OpenAI 兼容根路径"
            "（例如 https://xxx.cn-beijing.maas.aliyuncs.com/compatible-mode/v1）"
        )
    if "nodename nor servname" in lowered or "failed to resolve" in lowered:
        return "无法解析 API 地址，请检查 API Base 是否填写正确"
    return f"模型请求失败：{detail}"


def _status_error_message(status_code: int, body: str) -> str:
    text = (body or "")[:400]
    lowered = text.lower()
    if "either 'text' or 'image'" in lowered or "not both" in lowered:
        return (
            "当前接口把请求当成文生图：不能同时传文字和图片。"
            "请把读图/LLM 节点改用 Qwen 等多模态视觉对话模型，不要用 wan2.7-image-pro。"
        )
    return f"LLM request failed ({status_code}): {text[:300]}"


class LLMClient:
    def __init__(
        self,
        *,
        api_base: str,
        api_key: str,
        model: str,
        temperature: float | None = None,
        timeout_seconds: float = 120.0,
        mode: str = "fast",
    ) -> None:
        self.api_base = normalize_api_base(api_base)
        self.api_key = api_key or ""
        self.fixed_model = model
        self.fixed_temperature = temperature
        self.timeout_seconds = float(timeout_seconds or 120.0)
        self.default_mode = mode
        if not self.api_base or not self.api_key or not self.fixed_model:
            raise AppError(
                ErrorCode.INTERNAL,
                "LLM not configured: 请在「模型管理」中配置并启用对话模型",
                status_code=503,
            )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _model(self, mode: str) -> str:
        _ = mode
        return self.fixed_model

    def _temperature(self, mode: str) -> float:
        _ = mode
        if self.fixed_temperature is not None:
            return float(self.fixed_temperature)
        return 0.6 if self.default_mode != "deep" else 0.4

    def _http_timeout(self) -> httpx.Timeout:
        """流式时 read 是「两段数据间隔」；连接失败应尽快报错。"""
        read = max(30.0, float(self.timeout_seconds or 120.0))
        return httpx.Timeout(connect=20.0, write=60.0, read=read, pool=20.0)

    def _apply_generation_options(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload.setdefault("max_tokens", _DEFAULT_MAX_TOKENS)
        name = (self.fixed_model or "").lower()
        if "qwen" in name:
            # qwen3.x 默认开思考，非流式会先想数分钟才吐 JSON，触发 360s 超时。
            payload["enable_thinking"] = False
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        return payload

    @staticmethod
    def _drop_thinking_options(payload: dict[str, Any]) -> dict[str, Any]:
        payload.pop("enable_thinking", None)
        payload.pop("chat_template_kwargs", None)
        return payload

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        mode: str = "fast",
    ) -> AsyncIterator[str]:
        """SSE 流式输出纯文本 delta；内联 DSML 工具标记会被剥离。"""
        model = self._model(mode)
        temperature = self._temperature(mode)
        url = f"{self.api_base}/chat/completions"
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        payload = self._apply_generation_options(payload)
        n_mm = sum(1 for m in messages if isinstance(m.get("content"), list))
        n_img = sum(
            1
            for m in messages
            if isinstance(m.get("content"), list)
            for p in m["content"]
            if isinstance(p, dict) and p.get("type") == "image_url"
        )
        logger.info(
            "llm stream model=%s mode=%s multimodal_msgs=%s image_parts=%s",
            model,
            mode,
            n_mm,
            n_img,
        )

        timeout = self._http_timeout()
        try:
            for attempt in range(2):
                buf = ""
                reason_buf: list[str] = []
                had_content = False
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream(
                        "POST", url, headers=self._headers(), json=payload
                    ) as resp:
                        if resp.status_code >= 400:
                            body = (await resp.aread()).decode("utf-8", errors="ignore")
                            if (
                                attempt == 0
                                and resp.status_code == 400
                                and "enable_thinking" in payload
                            ):
                                logger.warning(
                                    "llm rejected thinking flags, retrying without them: %s",
                                    body[:200],
                                )
                                payload = self._drop_thinking_options(payload)
                                continue
                            raise AppError(
                                ErrorCode.INTERNAL,
                                _status_error_message(resp.status_code, body),
                                status_code=502,
                            )
                        async for line in resp.aiter_lines():
                            if not line or line.startswith(":"):
                                continue
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                obj = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            choices = obj.get("choices") or []
                            if not choices:
                                continue
                            content, reason = _choice_piece_text(choices[0])
                            if content:
                                had_content = True
                                buf += str(content)
                                if looks_like_dsml(buf):
                                    norm = normalize_dsml_delimiters(buf)
                                    if "</|DSML|tool_calls>" in norm:
                                        cleaned = strip_tool_call_markup(buf)
                                        if cleaned:
                                            yield cleaned
                                        buf = ""
                                    continue
                                yield str(content)
                                buf = ""
                            elif reason:
                                reason_buf.append(str(reason))
                        if buf.strip():
                            cleaned = strip_tool_call_markup(buf)
                            if cleaned:
                                yield cleaned
                        elif not had_content and reason_buf:
                            logger.info(
                                "llm stream used reasoning_content chars=%s model=%s",
                                sum(len(x) for x in reason_buf),
                                model,
                            )
                            yield "".join(reason_buf)
                        return
        except AppError:
            raise
        except httpx.TimeoutException as exc:
            raise AppError(
                ErrorCode.INTERNAL,
                f"模型请求超时（{self.timeout_seconds:.0f}s）",
                status_code=504,
            ) from exc
        except httpx.HTTPError as exc:
            logger.exception("llm stream http failed")
            raise AppError(
                ErrorCode.INTERNAL,
                _http_error_message(exc),
                status_code=502,
            ) from exc

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        mode: str = "fast",
    ) -> str:
        """工作流读图/出 JSON 走流式，避免思考模型长时间零输出触发整段超时。"""
        parts: list[str] = []
        async for text in self.stream_chat(messages, mode=mode):
            if text:
                parts.append(text)
        text = "".join(parts)
        if text.strip():
            return text
        logger.warning("llm stream empty, falling back to non-stream complete")
        result = await self.complete_message(messages, mode=mode)
        fallback = str(result.get("content") or "").strip()
        if fallback:
            return fallback
        return str(result.get("reasoning") or "")

    async def generate_image(
        self,
        prompt: str,
        *,
        size: str = "1024x1024",
    ) -> tuple[str, bytes]:
        """OpenAI 兼容 /images/generations；返回 (mime, bytes)。"""
        url = f"{self.api_base}/images/generations"
        payload: dict[str, Any] = {
            "model": self.fixed_model,
            "prompt": (prompt or "").strip() or "image",
            "n": 1,
            "size": size,
            "response_format": "b64_json",
        }
        timeout = httpx.Timeout(self.timeout_seconds)
        logger.info("llm image gen model=%s size=%s", self.fixed_model, size)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=self._headers(), json=payload)
                if resp.status_code >= 400:
                    raise AppError(
                        ErrorCode.INTERNAL,
                        f"文生图失败 ({resp.status_code}): {resp.text[:300]}",
                        status_code=502,
                    )
                try:
                    data = resp.json()
                except json.JSONDecodeError as exc:
                    raise AppError(
                        ErrorCode.INTERNAL,
                        "文生图接口返回无法解析。"
                        f" HTTP {resp.status_code} body={resp.text[:200]!r}",
                        status_code=502,
                    ) from exc
        except AppError:
            raise
        except httpx.TimeoutException as exc:
            raise AppError(
                ErrorCode.INTERNAL,
                f"文生图请求超时（{self.timeout_seconds:.0f}s）",
                status_code=504,
            ) from exc
        except httpx.HTTPError as exc:
            detail = (str(exc) or "").strip() or type(exc).__name__
            raise AppError(
                ErrorCode.INTERNAL,
                f"文生图请求失败：{detail}",
                status_code=502,
            ) from exc

        items = data.get("data") if isinstance(data, dict) else None
        first = items[0] if isinstance(items, list) and items else {}
        if not isinstance(first, dict):
            raise AppError(ErrorCode.INTERNAL, "文生图未返回图片", status_code=502)
        b64 = str(first.get("b64_json") or first.get("b64Json") or "").strip()
        if b64:
            try:
                raw = base64.b64decode(b64, validate=False)
            except Exception as exc:  # noqa: BLE001
                raise AppError(
                    ErrorCode.INTERNAL, "文生图 Base64 无效", status_code=502
                ) from exc
            if not raw:
                raise AppError(ErrorCode.INTERNAL, "文生图内容为空", status_code=502)
            return "image/png", raw
        img_url = str(first.get("url") or "").strip()
        if img_url.startswith("data:") and "," in img_url:
            header, payload_b64 = img_url.split(",", 1)
            mime = "image/png"
            if ";base64" in header:
                mime = header[5:].split(";", 1)[0].strip() or mime
            try:
                raw = base64.b64decode("".join(payload_b64.split()), validate=False)
            except Exception as exc:  # noqa: BLE001
                raise AppError(
                    ErrorCode.INTERNAL, "文生图 data URL 无效", status_code=502
                ) from exc
            return mime, raw
        if img_url.startswith("http://") or img_url.startswith("https://"):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    fetched = await client.get(img_url)
                    fetched.raise_for_status()
            except httpx.HTTPError as exc:
                raise AppError(
                    ErrorCode.INTERNAL, "下载生成图片失败", status_code=502
                ) from exc
            ctype = str(fetched.headers.get("content-type") or "image/png").split(";")[0]
            mime = ctype.strip() if ctype.startswith("image/") else "image/png"
            return mime, fetched.content
        raise AppError(ErrorCode.INTERNAL, "文生图未返回可用图片数据", status_code=502)

    async def complete_message(
        self,
        messages: list[dict[str, Any]],
        *,
        mode: str = "fast",
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """非流式补全；可选 tools。返回 {content, tool_calls}。"""
        model = self._model(mode)
        temperature = self._temperature(mode)
        url = f"{self.api_base}/chat/completions"
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        payload = self._apply_generation_options(payload)
        n_img = sum(
            1
            for m in messages
            if isinstance(m.get("content"), list)
            for p in m["content"]
            if isinstance(p, dict) and p.get("type") == "image_url"
        )
        logger.info(
            "llm complete model=%s mode=%s tools=%s image_parts=%s",
            model,
            mode,
            len(tools or []),
            n_img,
        )
        timeout = self._http_timeout()
        try:
            data: dict[str, Any] | None = None
            async with httpx.AsyncClient(timeout=timeout) as client:
                for attempt in range(2):
                    resp = await client.post(url, headers=self._headers(), json=payload)
                    if resp.status_code >= 400:
                        if (
                            attempt == 0
                            and resp.status_code == 400
                            and "enable_thinking" in payload
                        ):
                            logger.warning(
                                "llm rejected thinking flags, retrying without them: %s",
                                resp.text[:200],
                            )
                            payload = self._drop_thinking_options(payload)
                            continue
                        raise AppError(
                            ErrorCode.INTERNAL,
                            _status_error_message(resp.status_code, resp.text),
                            status_code=502,
                        )
                    try:
                        data = resp.json()
                    except json.JSONDecodeError as exc:
                        raise AppError(
                            ErrorCode.INTERNAL,
                            "模型接口返回空内容，无法解析 JSON。"
                            f" HTTP {resp.status_code} body={resp.text[:200]!r}",
                            status_code=502,
                        ) from exc
                    break
            if not isinstance(data, dict):
                raise AppError(ErrorCode.INTERNAL, "模型接口未返回 JSON", status_code=502)
        except AppError:
            raise
        except httpx.TimeoutException as exc:
            logger.warning(
                "llm complete timeout model=%s seconds=%s",
                model,
                self.timeout_seconds,
            )
            raise AppError(
                ErrorCode.INTERNAL,
                f"模型请求超时（{self.timeout_seconds:.0f}s）",
                status_code=504,
            ) from exc
        except httpx.HTTPError as exc:
            logger.exception("llm complete http failed")
            raise AppError(
                ErrorCode.INTERNAL,
                _http_error_message(exc),
                status_code=502,
            ) from exc
        message = (data.get("choices") or [{}])[0].get("message") or {}
        content, reason = _choice_piece_text({"message": message})
        if not content and reason:
            content = reason
        tool_calls_raw = message.get("tool_calls") or []
        tool_calls: list[dict[str, Any]] = []
        for tc in tool_calls_raw:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            tool_calls.append(
                {
                    "id": str(tc.get("id") or ""),
                    "name": str(fn.get("name") or ""),
                    "arguments": str(fn.get("arguments") or "{}"),
                }
            )

        # 部分模型（如豆包）把工具调用写成 DSML 文本而非标准 tool_calls
        if content and (not tool_calls or looks_like_dsml(content)):
            cleaned, parsed = parse_tool_calls_from_content(content)
            if parsed:
                logger.info(
                    "llm parsed %s inline tool call(s) from content (DSML/XML)",
                    len(parsed),
                )
                if not tool_calls:
                    tool_calls = parsed
                content = cleaned
            else:
                content = strip_tool_call_markup(content)

        return {
            "content": content,
            "tool_calls": tool_calls,
        }
