"""AI 对话与会话路由。

对话链路：限流 → 会话锁 → Redis 热窗口 →（可选 RAG）→ 提示词 → MCP 工具循环 → Stream 归档。
传 agentId 时以场景智能体配置覆盖 mode / prompt / 知识库 / 工具白名单。
未迁：数据治理检索、Qdrant 长期记忆、窑炉报告。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from api.deps import CurrentUser, DbSession
from api.services.ai import memory as memory_svc
from api.services.ai import sessions as sessions_svc
from api.services.ai.chat_images import (
    DEFAULT_IMAGE_PROMPT,
    decode_chat_images,
    multimodal_user_content,
    persist_chat_images,
)
from api.services.ai.prompts import build_system_prompt
from api.services.knowledge import access as kb_access
from api.services.knowledge.ingest import search_chunks
from api.services.mcp.runtime import run_chat_with_mcp
from api.services.models.runtime import build_llm_client
from common.config import get_settings
from common.errors import AppError, ErrorCode
from common.logging import get_logger
from common.redis_tools import (
    check_sliding_rate_limit,
    force_release_session_lock,
    session_lock,
)
from common.response import ok

router = APIRouter(prefix="/ai", tags=["ai"])
logger = get_logger(__name__)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system", "tool"] = "user"
    content: str


class ChatImageIn(BaseModel):
    mimeType: str = Field(default="image/jpeg", max_length=64)
    data: str = Field(min_length=8)


class ChatRequest(BaseModel):
    """优先 content + sessionId；messages 仅兼容旧客户端（取最后一条 user）。"""

    content: str | None = Field(default=None, max_length=20000)
    messages: list[ChatMessage] | None = None
    images: list[ChatImageIn] = Field(default_factory=list, max_length=4)
    mode: Literal["fast", "deep"] = "fast"
    sessionId: str = Field(min_length=1, max_length=32)
    useKnowledge: bool = False
    knowledgeBaseIds: list[str] = Field(default_factory=list, max_length=32)
    promptId: str | None = Field(default=None, max_length=32)
    agentId: str | None = Field(default=None, max_length=32)
    modelId: str | None = Field(default=None, max_length=32)


class RelatedRequest(BaseModel):
    question: str
    answer: str = ""


class CreateSessionRequest(BaseModel):
    title: str | None = Field(default=None, max_length=128)
    mode: Literal["fast", "deep"] = "fast"


class UpdateSessionRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    mode: Literal["fast", "deep"] | None = None


def _normalize_kb_ids(raw: list[str] | None) -> list[str]:
    return [str(x).strip() for x in (raw or []) if str(x).strip()][:32]


async def _resolve_chat_bindings(db: DbSession, body: ChatRequest) -> dict[str, Any]:
    """有 agentId 时以智能体配置为准，否则用请求体字段。"""
    agent_id = (body.agentId or "").strip() or None
    if not agent_id:
        kb_ids = _normalize_kb_ids(body.knowledgeBaseIds)
        if not kb_ids and body.useKnowledge:
            # 兼容旧客户端只传开关、不传库 id：仍视为未绑定，不检索
            kb_ids = []
        return {
            "agentId": None,
            "agentName": None,
            "mode": body.mode,
            "promptId": (body.promptId or "").strip() or None,
            "knowledgeBaseIds": kb_ids,
            "toolsEnabled": True,
            "allowedToolIds": None,
            "modelId": (body.modelId or "").strip() or None,
        }

    from api.services.agents import configs as agent_configs

    bundle = await agent_configs.get_enabled_bundle(db, agent_id)
    mode = bundle.get("mode") if bundle.get("mode") in ("fast", "deep") else "fast"
    tools_enabled = bool(bundle.get("toolsEnabled", True))
    mcp_ids = [
        str(x).strip()
        for x in (bundle.get("mcpToolIds") or [])
        if str(x).strip()
    ][:64]
    if not tools_enabled:
        allowed: list[str] | None = []
    elif mcp_ids:
        allowed = mcp_ids
    else:
        allowed = None

    return {
        "agentId": bundle["id"],
        "agentName": bundle.get("name"),
        "mode": mode,
        "promptId": (bundle.get("promptId") or "").strip() or None,
        "knowledgeBaseIds": _normalize_kb_ids(bundle.get("knowledgeBaseIds") or []),
        "toolsEnabled": tools_enabled,
        "allowedToolIds": allowed,
        "modelId": (body.modelId or "").strip() or None,
    }


@router.get("/status")
async def ai_status(db: DbSession, user: CurrentUser) -> dict:
    _ = user
    from api.services.models import configs as model_configs

    runtime = await model_configs.runtime_status(db)
    return ok(
        {
            "llm_configured": runtime["llm_configured"],
            "embedding_configured": runtime["embedding_configured"],
            "llm_fast": runtime["llm_fast"],
            "llm_deep": runtime["llm_deep"],
            "embedding": runtime["embedding"],
            "source": "model_configs",
        }
    )


@router.get("/sessions")
async def sessions_list(db: DbSession, user: CurrentUser) -> dict:
    items = await sessions_svc.list_sessions(db, user_id=user.id)
    return ok({"items": items})


@router.post("/sessions")
async def sessions_create(body: CreateSessionRequest, db: DbSession, user: CurrentUser) -> dict:
    item = await sessions_svc.create_session(
        db,
        user_id=user.id,
        title=body.title,
        mode=body.mode,
    )
    return ok({"item": item})


@router.get("/sessions/{session_id}")
async def sessions_get(session_id: str, db: DbSession, user: CurrentUser) -> dict:
    item = await sessions_svc.get_session_item_merged(
        db, public_id=session_id, user_id=user.id
    )
    return ok({"item": item})


@router.patch("/sessions/{session_id}")
async def sessions_update(
    session_id: str,
    body: UpdateSessionRequest,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    item = await sessions_svc.update_session(
        db,
        public_id=session_id,
        user_id=user.id,
        title=body.title,
        mode=body.mode,
    )
    return ok({"item": item})


@router.delete("/sessions/{session_id}")
async def sessions_delete(session_id: str, db: DbSession, user: CurrentUser) -> dict:
    await sessions_svc.delete_session(db, public_id=session_id, user_id=user.id)
    await force_release_session_lock(session_id)
    return ok({"deleted": True})


@router.post("/sessions/{session_id}/cancel")
async def sessions_cancel(session_id: str, db: DbSession, user: CurrentUser) -> dict:
    """前端点「停止」时调用：强制释放会话生成锁，便于立即重问。"""
    await sessions_svc.get_session_for_user(
        db,
        public_id=session_id,
        user_id=user.id,
        with_messages=False,
    )
    released = await force_release_session_lock(session_id)
    return ok({"cancelled": True, "lockReleased": released})


@router.post("/sessions/{session_id}/summarize-title")
async def sessions_summarize_title(session_id: str, db: DbSession, user: CurrentUser) -> dict:
    item = await sessions_svc.summarize_session_title(
        db,
        public_id=session_id,
        user_id=user.id,
        force=True,
    )
    return ok({"item": item})


def _extract_user_content(body: ChatRequest) -> str:
    if body.content and body.content.strip():
        return body.content.strip()
    if body.messages:
        last_user = next((m for m in reversed(body.messages) if m.role == "user"), None)
        if last_user and last_user.content.strip():
            return last_user.content.strip()
    return ""


@router.post("/chat")
async def ai_chat(
    body: ChatRequest,
    request: Request,
    db: DbSession,
    user: CurrentUser,
) -> EventSourceResponse:
    """SSE 对话：Redis 热窗口 + RAG + 提示词 + MCP 工具循环。"""
    settings = get_settings()
    await check_sliding_rate_limit(scope="chat", subject=str(user.id))
    bindings = await _resolve_chat_bindings(db, body)
    chat_mode: Literal["fast", "deep"] = bindings["mode"]
    prompt_id: str | None = bindings["promptId"]
    kb_ids_bind: list[str] = list(bindings["knowledgeBaseIds"])
    kb_ids_bind = await kb_access.require_usable_ids(db, user, kb_ids_bind)
    tools_enabled: bool = bool(bindings["toolsEnabled"])
    allowed_tool_ids: list[str] | None = bindings["allowedToolIds"]
    agent_id: str | None = bindings["agentId"]
    agent_name: str | None = bindings["agentName"]
    model_id: str | None = bindings.get("modelId")

    await build_llm_client(db, chat_mode, model_id=model_id)
    user_text = _extract_user_content(body)
    image_blobs = decode_chat_images([img.model_dump() for img in (body.images or [])])
    logger.info(
        "chat request session=%s images_in=%s text_len=%s",
        body.sessionId,
        len(image_blobs),
        len(user_text),
    )
    if not user_text and not image_blobs:
        raise AppError(ErrorCode.VALIDATION, "请输入问题或上传图片", status_code=422)

    session = await sessions_svc.get_session_for_user(
        db,
        public_id=body.sessionId,
        user_id=user.id,
        with_messages=False,
    )

    async def event_generator():  # noqa: ANN202
        async def _renew_loop(lock: Any) -> None:
            interval = max(15.0, float(getattr(lock, "ttl", 300) or 300) / 3.0)
            try:
                while True:
                    await asyncio.sleep(interval)
                    ok_renew = await lock.renew()
                    if not ok_renew:
                        logger.warning(
                            "chat session lock renew failed session=%s",
                            session.public_id,
                        )
                        return
            except asyncio.CancelledError:
                raise

        async def _watch_disconnect() -> None:
            try:
                while True:
                    if await request.is_disconnected():
                        logger.info(
                            "chat client disconnected, force release lock session=%s",
                            session.public_id,
                        )
                        await force_release_session_lock(session.public_id)
                        return
                    await asyncio.sleep(0.4)
            except asyncio.CancelledError:
                raise

        disconnect_task: asyncio.Task[None] | None = None
        try:
            async with session_lock(session.public_id, wait_seconds=1.0) as lock:
                renew_task = asyncio.create_task(_renew_loop(lock))
                disconnect_task = asyncio.create_task(_watch_disconnect())
                try:
                    await memory_svc.ensure_hot_context(db, session=session)

                    msg_id = memory_svc.short_msg_id()
                    saved_images = (
                        persist_chat_images(
                            session_public_id=session.public_id,
                            msg_id=msg_id,
                            blobs=image_blobs,
                        )
                        if image_blobs
                        else []
                    )
                    caption = user_text or (DEFAULT_IMAGE_PROMPT if saved_images else "")
                    user_hot = memory_svc.build_hot_message(
                        role="user",
                        content=caption,
                        mode=chat_mode,
                        images=saved_images,
                        knowledge_base_ids=list(kb_ids_bind),
                        msg_id=msg_id,
                    )
                    await memory_svc.append_hot_and_enqueue(
                        session=session, message=user_hot
                    )

                    chunks: list[dict[str, Any]] = []
                    kb_ids = list(kb_ids_bind)
                    await memory_svc.bump_session_meta(
                        db,
                        session=session,
                        mode=chat_mode,
                        knowledge_base_ids=kb_ids,
                    )
                    use_knowledge = len(kb_ids) > 0
                    # 仅图片无提问时不检索，避免用默认提示词污染召回
                    do_rag = use_knowledge and bool(user_text)
                    if do_rag:
                        try:
                            chunks = await search_chunks(
                                db,
                                query=user_text,
                                top_k=settings.kb_search_top_k,
                                min_score=settings.kb_search_min_score,
                                kb_ids=kb_ids,
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("rag search failed: %s", exc)
                            chunks = []

                    refs_payload: dict[str, Any] = {
                        "mode": chat_mode,
                        "chunks": chunks,
                        "useKnowledge": do_rag,
                        "knowledgeBaseIds": kb_ids,
                        "hasImages": bool(saved_images),
                    }
                    if agent_id:
                        refs_payload["agentId"] = agent_id
                        refs_payload["agentName"] = agent_name
                    yield {
                        "event": "refs",
                        "data": json.dumps(refs_payload, ensure_ascii=False),
                    }

                    hot_msgs = await memory_svc.load_hot_messages(session.public_id)
                    base_prompt: str | None = None
                    if prompt_id:
                        from api.services.prompts import configs as prompt_configs

                        base_prompt = await prompt_configs.get_enabled_content(
                            db, prompt_id
                        )
                    system_prompt = await build_system_prompt(
                        db,
                        chunks,
                        base_prompt=base_prompt,
                        use_knowledge=do_rag,
                        has_images=bool(saved_images),
                    )
                    llm_messages: list[dict[str, Any]] = []
                    if system_prompt:
                        llm_messages.append(
                            {"role": "system", "content": system_prompt}
                        )
                    llm_messages.extend(memory_svc.hot_messages_for_llm(hot_msgs))
                    if image_blobs:
                        vision_content = multimodal_user_content(caption, image_blobs)
                        replaced = False
                        for i in range(len(llm_messages) - 1, -1, -1):
                            if llm_messages[i].get("role") == "user":
                                llm_messages[i] = {
                                    "role": "user",
                                    "content": vision_content,
                                }
                                replaced = True
                                break
                        if not replaced:
                            llm_messages.append(
                                {"role": "user", "content": vision_content}
                            )
                        logger.info(
                            "chat vision attached images=%s bytes=%s parts=%s",
                            len(image_blobs),
                            sum(len(b) for _, b in image_blobs),
                            [p.get("type") for p in vision_content],
                        )

                    accumulated = ""
                    try:
                        client = await build_llm_client(db, chat_mode, model_id=model_id)
                        async for ev in run_chat_with_mcp(
                            db,
                            client=client,
                            llm_messages=llm_messages,
                            mode=chat_mode,
                            session=session,
                            user_id=user.id,
                            kb_ids=kb_ids,
                            chunks=chunks,
                            tools_enabled=tools_enabled,
                            allowed_tool_ids=allowed_tool_ids,
                        ):
                            if await request.is_disconnected():
                                logger.info(
                                    "chat aborted mid-stream session=%s",
                                    session.public_id,
                                )
                                await force_release_session_lock(session.public_id)
                                return
                            if ev.get("event") == "__final__":
                                try:
                                    final = json.loads(ev.get("data") or "{}")
                                    accumulated = str(
                                        final.get("content") or accumulated
                                    )
                                except json.JSONDecodeError:
                                    pass
                                continue
                            yield ev

                        title: str | None = None
                        if accumulated.strip():
                            assistant_hot = memory_svc.build_hot_message(
                                role="assistant",
                                content=accumulated,
                                mode=chat_mode,
                                refs=(
                                    chunks
                                    if chunks
                                    else ([{"_miss": True, "kb_ids": kb_ids}] if do_rag else [])
                                ),
                                knowledge_base_ids=kb_ids,
                                model_name=getattr(client, "fixed_model", None),
                            )
                            await memory_svc.append_hot_and_enqueue(
                                session=session, message=assistant_hot
                            )
                            await memory_svc.bump_session_meta(
                                db,
                                session=session,
                                mode=chat_mode,
                                knowledge_base_ids=kb_ids,
                            )
                            try:
                                await memory_svc.maybe_roll_trim(db, session=session)
                            except Exception as exc:  # noqa: BLE001
                                logger.warning("roll trim failed: %s", exc)
                            title = await sessions_svc.maybe_auto_title(
                                db, session=session
                            )

                        done_payload: dict[str, Any] = {
                            "ok": True,
                            "sessionId": session.public_id,
                        }
                        if title:
                            done_payload["title"] = title
                        if agent_id:
                            done_payload["agentId"] = agent_id
                        yield {
                            "event": "done",
                            "data": json.dumps(done_payload, ensure_ascii=False),
                        }
                    except AppError as exc:
                        yield {
                            "event": "error",
                            "data": json.dumps(
                                {"msg": exc.msg, "code": exc.code},
                                ensure_ascii=False,
                            ),
                        }
                    except asyncio.CancelledError:
                        await force_release_session_lock(session.public_id)
                        raise
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("ai chat failed")
                        yield {
                            "event": "error",
                            "data": json.dumps({"msg": str(exc)}, ensure_ascii=False),
                        }
                finally:
                    renew_task.cancel()
                    try:
                        await renew_task
                    except asyncio.CancelledError:
                        pass
                    except Exception:  # noqa: BLE001
                        pass
        except AppError as exc:
            yield {
                "event": "error",
                "data": json.dumps(
                    {"msg": exc.msg, "code": exc.code}, ensure_ascii=False
                ),
            }
        except asyncio.CancelledError:
            await force_release_session_lock(session.public_id)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("ai chat event_generator failed")
            yield {
                "event": "error",
                "data": json.dumps({"msg": str(exc)}, ensure_ascii=False),
            }
        finally:
            if disconnect_task is not None:
                disconnect_task.cancel()
                try:
                    await disconnect_task
                except asyncio.CancelledError:
                    pass
                except Exception:  # noqa: BLE001
                    pass

    return EventSourceResponse(event_generator())


@router.post("/chat/related")
async def ai_chat_related(body: RelatedRequest, db: DbSession, user: CurrentUser) -> dict:
    _ = user
    prompt = (
        "你是智能助手。基于用户的「原问题」和「AI 回答」，"
        "生成 4 条用户可能进一步追问的相关问题。要求：\n"
        "1. 每条不超过 30 字；\n"
        "2. 紧扣原问题主题；\n"
        "3. 不要重复原问题；\n"
        "4. 只输出 JSON 数组字符串，例如 [\"问题1\",\"问题2\",\"问题3\",\"问题4\"]。"
    )
    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": f"原问题：{body.question}\n\nAI 回答：{body.answer[:2000]}",
        },
    ]
    client = await build_llm_client(db, "fast")
    raw = await client.complete(messages, mode="fast")
    questions: list[str] = []
    try:
        start = raw.find("[")
        end = raw.rfind("]")
        if start >= 0 and end > start:
            parsed = json.loads(raw[start : end + 1])
            if isinstance(parsed, list):
                questions = [str(x).strip() for x in parsed if str(x).strip()][:4]
    except json.JSONDecodeError:
        questions = []
    return ok({"questions": questions})
