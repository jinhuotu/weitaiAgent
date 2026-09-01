"""工作流执行：按边动态行走（条件节点走 yes/no），产出 SSE。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.services.ai import memory as memory_svc
from api.services.ai import sessions as sessions_svc
from api.services.ai.chat_images import (
    DEFAULT_IMAGE_PROMPT,
    output_images_to_blobs,
    persist_chat_images,
    state_images_from_input,
)
from api.services.workflows import crud as crud_svc
from api.services.workflows import nodes as nodes_svc
from common.errors import AppError, ErrorCode
from common.logging import get_logger
from common.redis_tools import SessionLock
from db.models.chat import ChatSession
from db.models.workflow import WorkflowRun, WorkflowRunStep

logger = get_logger(__name__)

MAX_STEPS = 48
_YES_HANDLES = frozenset({"yes", "true", "1"})
_NO_HANDLES = frozenset({"no", "false", "0"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sse(event: str, payload: dict[str, Any]) -> dict[str, str]:
    return {
        "event": event,
        "data": json.dumps(payload, ensure_ascii=False, default=str),
    }


def _find_start(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    starts = [n for n in nodes if str(n.get("type") or "") == "start"]
    if len(starts) != 1:
        raise AppError(
            ErrorCode.VALIDATION,
            "graph must have exactly one start node",
            status_code=422,
        )
    return starts[0]


def _outgoing_edges(
    edges: list[dict[str, Any]], src: str
) -> list[dict[str, Any]]:
    return [e for e in edges if str(e.get("source") or "").strip() == src]


def pick_next_node_id(
    node: dict[str, Any],
    edges: list[dict[str, Any]],
    state: dict[str, Any],
) -> str:
    """条件节点按 yes/no handle 选边，其余取第一条出边。"""
    nid = str(node.get("id") or "").strip()
    ntype = str(node.get("type") or "").strip()
    if ntype == "end":
        return ""
    outs = _outgoing_edges(edges, nid)
    if ntype == "condition":
        matched = bool(state.get("lastCondition"))
        want = _YES_HANDLES if matched else _NO_HANDLES
        for e in outs:
            handle = str(e.get("sourceHandle") or "").strip().lower()
            if handle in want:
                return str(e.get("target") or "").strip()
        branch = "yes" if matched else "no"
        raise AppError(
            ErrorCode.VALIDATION,
            f"condition node {nid}: missing {branch} branch",
            status_code=422,
        )
    if not outs:
        raise AppError(
            ErrorCode.VALIDATION,
            f"node {nid} has no outgoing edge",
            status_code=422,
        )
    return str(outs[0].get("target") or "").strip()


def _input_query(input_data: Any) -> str:
    if isinstance(input_data, dict):
        return str(input_data.get("query") or input_data.get("text") or "").strip()
    return str(input_data or "").strip()


def _compact_images(images: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(images, list):
        return out
    for item in images:
        if not isinstance(item, dict):
            continue
        mime = str(item.get("mimeType") or "image/jpeg")
        url = str(item.get("dataUrl") or "")
        row: dict[str, Any] = {"mimeType": mime, "approxBytes": max(0, (len(url) * 3) // 4)}
        key = str(item.get("fileKey") or "").strip()
        if key:
            row["fileKey"] = key
        out.append(row)
    return out


def _state_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "output": state.get("output"),
        "query": state.get("query"),
        "context": state.get("context"),
        "refs": state.get("refs"),
        "lastToolResult": state.get("lastToolResult"),
        "vars": state.get("vars"),
        "visionText": state.get("visionText"),
        "lastSavedPath": state.get("lastSavedPath"),
        "lastCondition": state.get("lastCondition"),
        "layoutKind": (
            (state.get("layout") or {}).get("kind")
            if isinstance(state.get("layout"), dict)
            else None
        ),
        "outputImages": _compact_images(state.get("outputImages")),
        "imageCount": len(state.get("images") or []),
    }


async def _persist_user_turn(
    db: AsyncSession,
    *,
    session: ChatSession,
    state: dict[str, Any],
    mode: str,
) -> None:
    await memory_svc.ensure_hot_context(db, session=session)
    blobs = output_images_to_blobs(state.get("images"))
    msg_id = memory_svc.short_msg_id()
    saved = (
        persist_chat_images(
            session_public_id=session.public_id,
            msg_id=msg_id,
            blobs=blobs,
        )
        if blobs
        else []
    )
    caption = _input_query(state.get("input")) or (DEFAULT_IMAGE_PROMPT if saved else "")
    user_hot = memory_svc.build_hot_message(
        role="user",
        content=caption,
        mode=mode,
        images=saved,
        msg_id=msg_id,
    )
    await memory_svc.append_hot_and_enqueue(session=session, message=user_hot)
    await memory_svc.bump_session_meta(db, session=session, mode=mode)


async def _persist_assistant_turn(
    db: AsyncSession,
    *,
    session: ChatSession,
    state: dict[str, Any],
    mode: str,
) -> str | None:
    content = nodes_svc.as_text(state.get("output")).strip()
    blobs = output_images_to_blobs(state.get("outputImages"))
    if not content and not blobs:
        return None
    msg_id = memory_svc.short_msg_id()
    saved = (
        persist_chat_images(
            session_public_id=session.public_id,
            msg_id=msg_id,
            blobs=blobs,
        )
        if blobs
        else []
    )
    refs = state.get("refs") if isinstance(state.get("refs"), list) else []
    layout_plan = state.get("layout") if isinstance(state.get("layout"), dict) else None
    assistant_hot = memory_svc.build_hot_message(
        role="assistant",
        content=content or "（见附图）",
        mode=mode,
        refs=refs,
        images=saved,
        msg_id=msg_id,
        attachments=state.get("layoutFiles")
        if isinstance(state.get("layoutFiles"), list)
        else None,
        layout_plan=layout_plan,
    )
    await memory_svc.append_hot_and_enqueue(session=session, message=assistant_hot)
    await memory_svc.bump_session_meta(db, session=session, mode=mode)
    try:
        await memory_svc.maybe_roll_trim(db, session=session)
    except Exception as exc:  # noqa: BLE001
        logger.warning("workflow chat roll trim failed: %s", exc)
    try:
        return await sessions_svc.maybe_auto_title(db, session=session)
    except Exception as exc:  # noqa: BLE001
        logger.warning("workflow chat title failed: %s", exc)
        return None


async def _safe_commit(db: AsyncSession) -> None:
    try:
        await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("workflow commit failed")
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            logger.exception("workflow rollback failed")


async def _execute_node_streaming(
    db: AsyncSession,
    *,
    node: dict[str, Any],
    state: dict[str, Any],
    run_id: str,
) -> AsyncIterator[dict[str, Any]]:
    """yield SSE dicts；最后一条 kind=detail。不另开 asyncio.Task。"""
    ntype = str(node.get("type") or "")
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    if ntype != "agent":
        detail = await nodes_svc.execute_node(db, node=node, state=state)
        yield {"kind": "detail", "detail": detail}
        return
    async for item in nodes_svc.iter_agent_node(db, data=data, state=state):
        if item.get("kind") == "sse":
            payload = dict(item.get("payload") or {})
            payload.setdefault("runId", run_id)
            yield {"kind": "sse", "event": item.get("event"), "payload": payload}
        else:
            yield item


async def run_workflow(
    db: AsyncSession,
    *,
    workflow_public_id: str,
    input_data: Any,
    use_draft: bool = False,
    created_by: int | None = None,
    trigger: str = "trial",
    session_id: str | None = None,
) -> AsyncIterator[dict[str, str]]:
    """创建 run/steps，按条件动态行走并 yield SSE。session_id 时写入对话热记忆。"""
    workflow = await crud_svc.get_by_public_id(db, workflow_public_id)
    if not workflow.enabled:
        raise AppError(ErrorCode.VALIDATION, "workflow is disabled", status_code=422)

    chat_trigger = bool(session_id) or trigger == "chat"
    if chat_trigger:
        use_draft = False

    version = await crud_svc.resolve_run_version(db, workflow, use_draft=use_draft)
    graph = (
        version.graph_json
        if isinstance(version.graph_json, dict)
        else crud_svc.default_empty_graph()
    )
    graph = crud_svc.validate_graph(graph)
    nodes = list(graph["nodes"])
    edges = list(graph["edges"])
    by_id = {str(n.get("id")): n for n in nodes}
    _find_start(nodes)

    session: ChatSession | None = None
    if session_id:
        if created_by is None:
            raise AppError(ErrorCode.UNAUTHORIZED, "login required", status_code=401)
        session = await sessions_svc.get_session_for_user(
            db,
            public_id=str(session_id).strip(),
            user_id=int(created_by),
            with_messages=False,
        )

    async def _run_body() -> AsyncIterator[dict[str, str]]:
        if isinstance(input_data, dict):
            input_json: dict[str, Any] | Any = input_data
        else:
            input_json = {"text": input_data}

        run = WorkflowRun(
            public_id=crud_svc.short_id(12),
            workflow_id=workflow.id,
            version_id=version.id,
            status="pending",
            trigger="chat" if session else trigger,
            input_json=input_json if isinstance(input_json, dict) else {"value": input_json},
            created_by=created_by,
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)

        images: list[dict[str, str]] = []
        if isinstance(input_data, dict) and input_data.get("images"):
            images = state_images_from_input(input_data.get("images"))

        prior_layout = None
        if session is not None:
            from api.services.layouts.session_store import load_prior_layout

            prior_layout = await load_prior_layout(session.public_id)

        from api.services.layouts.revise import has_spatial_revise_intent, is_layout_revise_query

        query_text = _input_query(input_data)
        has_prior = isinstance(prior_layout, dict)
        layout_revise = is_layout_revise_query(
            query_text,
            has_prior=has_prior,
            has_images=bool(images),
        )
        # 有上一张 + 明确空间/改图意图时，强制走程序修订（不依赖 Redis 标记是否完整）
        if has_prior and not layout_revise and has_spatial_revise_intent(query_text):
            layout_revise = True
        if (
            not layout_revise
            and not has_prior
            and has_spatial_revise_intent(query_text)
        ):
            logger.info(
                "layout spatial intent without priorLayout session=%s q=%s",
                getattr(session, "public_id", None),
                query_text[:80],
            )

        state: dict[str, Any] = {
            "input": input_data,
            "query": query_text,
            "context": "",
            "output": None,
            "refs": [],
            "vars": {},
            "lastToolResult": None,
            "images": images,
            "refImages": [],
            "outputImages": [],
            "visionText": "",
            "lastSavedPath": "",
            "lastCondition": None,
            "chatSession": session,
            "persistHot": bool(session),
            "userId": int(created_by or 0),
            "runId": run.public_id,
            "priorLayout": prior_layout,
            "layoutRevise": layout_revise,
            "preferredModelId": (
                str(input_data.get("modelId") or "").strip()
                if isinstance(input_data, dict)
                else ""
            ),
        }

        run.status = "running"
        run.started_at = _now()
        await db.commit()

        title: str | None = None
        try:
            if session:
                await _persist_user_turn(db, session=session, state=state, mode="fast")

            cur = str(_find_start(nodes).get("id"))
            visited: set[str] = set()
            steps_run = 0
            reached_end = False

            while cur:
                if steps_run >= MAX_STEPS:
                    raise AppError(
                        ErrorCode.VALIDATION,
                        f"workflow exceeded {MAX_STEPS} steps",
                        status_code=422,
                    )
                if cur in visited:
                    raise AppError(
                        ErrorCode.VALIDATION,
                        f"cycle detected at node {cur}",
                        status_code=422,
                    )
                visited.add(cur)
                node = by_id.get(cur)
                if node is None:
                    raise AppError(
                        ErrorCode.VALIDATION, f"missing node {cur}", status_code=422
                    )

                node_id = str(node.get("id"))
                node_type = str(node.get("type") or "")
                step = WorkflowRunStep(
                    run_id=run.id,
                    node_id=node_id,
                    node_type=node_type,
                    status="running",
                    started_at=_now(),
                )
                db.add(step)
                await db.commit()
                await db.refresh(step)

                yield _sse(
                    "step_start",
                    {
                        "runId": run.public_id,
                        "nodeId": node_id,
                        "nodeType": node_type,
                        "stepId": step.id,
                    },
                )

                try:
                    detail: dict[str, Any] | None = None
                    async for item in _execute_node_streaming(
                        db, node=node, state=state, run_id=run.public_id
                    ):
                        if item.get("kind") == "sse":
                            yield _sse(str(item.get("event")), dict(item.get("payload") or {}))
                        elif item.get("kind") == "detail":
                            detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
                    step.status = "done"
                    step.detail_json = detail or {}
                    step.finished_at = _now()
                    await db.commit()
                    yield _sse(
                        "step_end",
                        {
                            "runId": run.public_id,
                            "nodeId": node_id,
                            "nodeType": node_type,
                            "stepId": step.id,
                            "status": "done",
                            "detail": detail or {},
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    step.status = "failed"
                    step.detail_json = {"error": str(exc)}
                    step.finished_at = _now()
                    run.status = "failed"
                    run.error_msg = str(exc)[:512]
                    run.finished_at = _now()
                    run.output_json = _state_snapshot(state)
                    await _safe_commit(db)
                    yield _sse(
                        "step_end",
                        {
                            "runId": run.public_id,
                            "nodeId": node_id,
                            "nodeType": node_type,
                            "stepId": step.id,
                            "status": "failed",
                            "detail": {"error": str(exc)},
                        },
                    )
                    yield _sse(
                        "error",
                        {"runId": run.public_id, "msg": str(exc), "nodeId": node_id},
                    )
                    return

                steps_run += 1
                if node_type == "end":
                    reached_end = True
                    break
                nxt = pick_next_node_id(node, edges, state)
                if not nxt:
                    break
                cur = nxt

            if not reached_end:
                raise AppError(
                    ErrorCode.VALIDATION,
                    "graph path from start does not reach an end node",
                    status_code=422,
                )

            if session:
                title = await _persist_assistant_turn(
                    db, session=session, state=state, mode="fast"
                )

            run.status = "done"
            run.finished_at = _now()
            run.output_json = _state_snapshot(state)
            await db.commit()
            done_payload: dict[str, Any] = {
                "ok": True,
                "runId": run.public_id,
                "status": "done",
                "output": state.get("output"),
                "outputImages": state.get("outputImages") or [],
                "layoutFiles": state.get("layoutFiles") or [],
            }
            if session:
                done_payload["sessionId"] = session.public_id
            if title:
                done_payload["title"] = title
            yield _sse("done", done_payload)
        except Exception as exc:  # noqa: BLE001
            logger.exception("workflow run failed run=%s", run.public_id)
            run.status = "failed"
            run.error_msg = str(exc)[:512]
            run.finished_at = _now()
            run.output_json = _state_snapshot(state)
            await _safe_commit(db)
            yield _sse("error", {"runId": run.public_id, "msg": str(exc)})

    lock: SessionLock | None = None
    if session:
        lock = SessionLock(session.public_id)
        acquired = await lock.acquire(wait_seconds=1.0)
        if not acquired:
            raise AppError(
                ErrorCode.CONFLICT,
                "该会话正在生成回复，请稍后再试（若刚点过停止，请再试一次或新建会话）",
                status_code=409,
            )
    try:
        async for ev in _run_body():
            yield ev
    except Exception as exc:  # noqa: BLE001
        logger.exception("workflow sse generator failed")
        yield _sse("error", {"msg": str(getattr(exc, "msg", None) or exc)})
    finally:
        if lock is not None:
            await lock.release()
