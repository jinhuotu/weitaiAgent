"""会话内上一张布置：改参时在旧 JSON 上改，不整张重画。"""

from __future__ import annotations

import json
import logging
from typing import Any

from common.config import get_settings
from common.redis_client import get_redis

logger = logging.getLogger(__name__)

_KEY = "chat:layout:{sid}"


def layout_session_key(session_public_id: str) -> str:
    return _KEY.format(sid=session_public_id)


def _as_layout_dict(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    if data.get("kind") != "ev_charging_station_plan":
        return None
    if not isinstance(data.get("parkingRows"), list):
        return None
    return data


async def load_prior_layout_from_hot(session_public_id: str | None) -> dict[str, Any] | None:
    """专用 key 丢失时，从热消息里最近一条带 layoutPlan 的助手消息恢复。"""
    sid = (session_public_id or "").strip()
    if not sid:
        return None
    try:
        from api.services.ai.memory import load_hot_messages

        msgs = await load_hot_messages(sid)
    except Exception:  # noqa: BLE001
        logger.warning("load prior layout from hot failed sid=%s", sid, exc_info=True)
        return None
    for msg in reversed(msgs or []):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        plan = _as_layout_dict(msg.get("layoutPlan"))
        if plan is not None:
            return plan
    return None


async def load_prior_layout(session_public_id: str | None) -> dict[str, Any] | None:
    sid = (session_public_id or "").strip()
    if not sid:
        return None
    redis = get_redis()
    raw = await redis.get(layout_session_key(sid))
    if raw:
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            data = None
        plan = _as_layout_dict(data)
        if plan is not None:
            return plan
    fallback = await load_prior_layout_from_hot(sid)
    if fallback is not None:
        try:
            await save_prior_layout(sid, fallback)
        except Exception:  # noqa: BLE001
            logger.warning("backfill prior layout failed sid=%s", sid, exc_info=True)
        return fallback
    return None


async def save_prior_layout(session_public_id: str | None, layout: dict[str, Any]) -> None:
    sid = (session_public_id or "").strip()
    if not sid or not isinstance(layout, dict):
        return
    if layout.get("kind") != "ev_charging_station_plan":
        return
    redis = get_redis()
    ttl = int(getattr(get_settings(), "chat_session_ttl_seconds", 7 * 24 * 3600) or 604800)
    await redis.set(layout_session_key(sid), json.dumps(layout, ensure_ascii=False), ex=ttl)


async def clear_prior_layout(session_public_id: str | None) -> None:
    sid = (session_public_id or "").strip()
    if not sid:
        return
    redis = get_redis()
    await redis.delete(layout_session_key(sid))
