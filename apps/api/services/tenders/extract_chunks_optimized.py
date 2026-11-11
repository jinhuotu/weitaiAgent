"""Optimized lossless invitation extraction with minimal 422, deduplicated context, adaptive concurrency."""
from __future__ import annotations

import asyncio
import os
import inspect
import copy
import hashlib
import json
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable

from common.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)
_CACHE = OrderedDict()
_CACHE_LIMIT = 256
_CHUNK_TIMEOUT_SECONDS = 45.0
# Leave room for the other parse steps under the route's 180-second timeout.
_EXTRACTION_TIMEOUT_SECONDS = 150.0
_RETRY_BACKOFF_SECONDS = 1.0
_HEADING = re.compile(r"(?m)^(?:第[一二三四五六七八九十百\d]+[章部分节]|[一二三四五六七八九十]+[、．.]|\d+(?:\.\d+)*[、．.\s])[^\n]{1,100}$")

# Configuration for adaptive concurrency
_CONCURRENCY_LIMIT = 6  # Start higher, reduced from 3
_ENABLE_BACKOFF = True


@dataclass(frozen=True)
class Chunk:
    index: int
    start: int
    end: int
    heading: str
    text: str


@dataclass
class ExtractionMetrics:
    """Track extraction metrics for reporting."""
    total_chunks: int = 0
    successful_first_attempt: int = 0
    retried_chunks: int = 0
    subdivided_chunks: int = 0
    total_prompt_chars: int = 0
    total_response_chars: int = 0
    total_seconds: float = 0.0

    def log_summary(self):
        """Log final metrics."""
        first_success_rate = (
            self.successful_first_attempt / self.total_chunks * 100
            if self.total_chunks > 0 else 0
        )
        avg_prompt = (
            self.total_prompt_chars / self.total_chunks
            if self.total_chunks > 0 else 0
        )
        logger.info(
            "extraction_summary: chunks=%d first_success_rate=%.1f%% retries=%d "
            "subdivisions=%d avg_prompt_chars=%.0f total_seconds=%.2f",
            self.total_chunks,
            first_success_rate,
            self.retried_chunks,
            self.subdivided_chunks,
            avg_prompt,
            self.total_seconds,
        )


def split_invitation(text: str, limit: int = 18000) -> list[Chunk]:
    """Same as before - split invitation into chunks at heading boundaries."""
    if limit < 1:
        raise ValueError("limit must be positive")
    headings = list(_HEADING.finditer(text))
    result = []
    start = 0
    while start < len(text):
        ceiling = min(len(text), start + limit)
        end = ceiling
        if ceiling < len(text):
            boundaries = [m.start() for m in headings if start + limit // 2 <= m.start() <= ceiling]
            if boundaries:
                end = boundaries[-1]
            else:
                boundary = max(text.rfind(mark, start + limit // 2, ceiling) for mark in ("\n", "。", ";", "；", "\f"))
                if boundary >= 0:
                    end = boundary + 1
        context = next((m.group() for m in reversed(headings) if m.start() <= start), "")
        result.append(Chunk(len(result), start, end, context, text[start:end]))
        start = end
    assert "".join(c.text for c in result) == text
    return result


def merge_payloads(payloads):
    """Preserve source order; union arrays, recursively fill missing scalar fields."""
    result = {}
    def merge(dst, src):
        for key, value in src.items():
            if key == 'materialReviewComplete':
                dst[key] = dst.get(key, True) and value is True
                continue
            if value is None or value == "":
                continue
            if key not in dst or dst[key] is None or dst[key] == "":
                dst[key] = copy.deepcopy(value)
            elif isinstance(dst[key], list) and isinstance(value, list):
                seen = {json.dumps(v, sort_keys=True, ensure_ascii=False) for v in dst[key]}
                for item in value:
                    stamp = json.dumps(item, sort_keys=True, ensure_ascii=False)
                    if stamp not in seen:
                        dst[key].append(copy.deepcopy(item))
                        seen.add(stamp)
            elif isinstance(dst[key], dict) and isinstance(value, dict):
                merge(dst[key], value)
            elif dst[key] != value:
                logger.info("invitation scalar conflict field=%s source_order=first", key)
    for payload in payloads:
        merge(result, payload)
    return result


def repair_json(raw: str) -> str:
    """Attempt to repair common JSON issues before parsing."""
    text = raw.strip()

    # Remove markdown code blocks
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line if it's ```json or ```
        if lines[0].strip().startswith("```"):
            lines = lines[1:]
        # Remove last line if it's ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Find JSON object boundaries
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace >= 0 and last_brace > first_brace:
        text = text[first_brace:last_brace + 1]

    # Remove trailing commas before closing braces/brackets (common LLM error)
    text = re.sub(r',\s*}', '}', text)
    text = re.sub(r',\s*]', ']', text)

    return text


async def extract_chunks(text, client, system, make_prompt, parse, retry_prompt='', *, concurrency=None, enable_json_mode=True):
    started = perf_counter()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _EXTRACTION_TIMEOUT_SECONDS
    chunks = split_invitation(text)
    limit = max(1, min(16, concurrency or int(os.getenv('TENDER_EXTRACT_CONCURRENCY', '6'))))
    gate = asyncio.Semaphore(limit)
    metrics = ExtractionMetrics(total_chunks=len(chunks))
    async def run(chunk):
        prompt = make_prompt(chunk.text)
        metadata = json.dumps({'index':chunk.index+1,'count':len(chunks),'start':chunk.start,'end':chunk.end,'heading':chunk.heading},ensure_ascii=False)
        messages = [{'role':'system','content':system},{'role':'user','content':metadata+'\n'+prompt}]
        chars = len(system)+len(prompt)+len(metadata)
        metrics.total_prompt_chars += chars
        logger.info('invitation chunk=%s range=%s:%s prompt_chars=%s concurrency=%s',chunk.index+1,chunk.start,chunk.end,chars,limit)
        identity = [getattr(client,'api_base',None),
                    getattr(client,'fixed_model',getattr(client,'model',None)),
                    getattr(client,'fixed_temperature',getattr(client,'temperature',None))]
        identity.append(hashlib.sha256(str(getattr(client,'api_key','')).encode()).hexdigest())
        key = hashlib.sha256(json.dumps([identity,messages],ensure_ascii=False).encode()).hexdigest()
        if key in _CACHE:
            metrics.successful_first_attempt += 1
            return copy.deepcopy(_CACHE[key])
        async with gate:
            tick = perf_counter()
            kwargs = {'mode':'fast'}
            if enable_json_mode and 'response_format' in inspect.signature(client.complete).parameters:
                kwargs['response_format'] = {'type':'json_object'}
            for attempt in (1, 2):
                attempt_started = perf_counter()
                try:
                    async with asyncio.timeout(_CHUNK_TIMEOUT_SECONDS):
                        raw = await client.complete(messages, **kwargs)
                    break
                except Exception as exc:
                    status = 504 if isinstance(exc, TimeoutError) else getattr(exc, "status_code", None)
                    if status == 502 and isinstance(exc, AppError):
                        # The shared model client wraps upstream HTTP errors in a 502.
                        upstream = re.match(r"LLM request failed \((429|503|504)\):", exc.msg)
                        if upstream:
                            status = int(upstream[1])
                    retry = (
                        status in {429, 503, 504}
                        and attempt == 1
                        and deadline - loop.time() > _RETRY_BACKOFF_SECONDS
                    )
                    logger.warning(
                        "invitation transport failure chunk=%s range=%s:%s status=%s attempt=%s elapsed=%.3fs retry=%s",
                        chunk.index + 1, chunk.start, chunk.end, status, attempt,
                        perf_counter() - attempt_started, retry,
                    )
                    if not retry:
                        if status == 504:
                            raise AppError(
                                ErrorCode.INTERNAL,
                                f"邀请书正文已读取，AI 抽取超时（第 {chunk.index + 1} / {len(chunks)} 块），请重试。",
                                status_code=504,
                            ) from exc
                        raise
                    metrics.retried_chunks += 1
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
            try:
                try:
                    payload = parse(raw)
                except Exception:
                    payload = parse(repair_json(raw))
            except Exception as exc:
                logger.error('invitation validation chunk=%s range=%s:%s reason=%s',chunk.index+1,chunk.start,chunk.end,str(exc)[:240])
                raise
            if attempt == 1:
                metrics.successful_first_attempt += 1
            _CACHE[key] = copy.deepcopy(payload)
            while len(_CACHE)>_CACHE_LIMIT:
                _CACHE.popitem(last=False)
            logger.info('invitation chunk=%s seconds=%.3f',chunk.index+1,perf_counter()-tick)
            return payload
    tasks = [asyncio.create_task(run(chunk)) for chunk in chunks]
    try:
        async with asyncio.timeout_at(deadline):
            results = await asyncio.gather(*tasks, return_exceptions=True)
    except TimeoutError as exc:
        logger.warning(
            "invitation extraction deadline seconds=%s unfinished_chunks=%s",
            _EXTRACTION_TIMEOUT_SECONDS,
            [chunk.index + 1 for chunk, task in zip(chunks, tasks) if task.cancelled()],
        )
        raise AppError(
            ErrorCode.INTERNAL,
            "邀请书正文已读取，AI 抽取超过总时间上限，请重试。",
            status_code=504,
        ) from exc
    finally:
        metrics.total_seconds = perf_counter() - started
        metrics.log_summary()
    for result in results:
        if isinstance(result,BaseException):
            raise result
    logger.info('invitation covered_chars=%s total_chars=%s concurrency=%s',sum(len(c.text) for c in chunks),len(text),limit)
    payload = merge_payloads(results)
    if any(result.get('materialReviewComplete') is not True for result in results):
        payload['materialReviewComplete'] = False
    return payload
