"""工作流节点执行：按类型变更共享 state。

可执行：start / knowledge / llm / agent / mcp / condition / vision /
image_out / layout_out / end。
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.services.agents import configs as agent_configs
from api.services.ai.chat_images import (
    DEFAULT_IMAGE_PROMPT,
    MAX_IMAGES,
    blobs_to_state_images,
    llm_image_parts,
    read_local_image,
)
from api.services.knowledge.chunking import summarize_layout_case_for_prompt
from api.services.knowledge.ingest import search_knowledge_docs
from api.services.layouts import parse_plan, plan_summary, prepare_plan, render_plan_png
from api.services.layouts.brief import (
    apply_layout_brief,
    attach_site_polygon,
    parse_layout_brief,
    prune_unmentioned_site_context,
)
from api.services.layouts.cad import is_cad_path, rasterize_dxf
from api.services.layouts.check import check_plan, issues_as_notes, repair_plan
from api.services.layouts.rules import default_sheet_notes
from api.services.layouts.svg import write_plan_svg
from api.services.mcp import servers as mcp_servers
from api.services.mcp.runtime import run_chat_with_mcp
from api.services.models.runtime import build_llm_client
from api.services.prompts import configs as prompt_configs
from common.errors import AppError, ErrorCode
from db.models.mcp import McpServer, McpTool

OnEvent = Callable[[str, dict[str, Any]], Awaitable[None]]

_NEED_IMAGE_HINTS = (
    "需要配图",
    "需要图片",
    "生成图片",
    "生成配图",
    "效果图",
    "出图",
    '"needimage": true',
    '"need_image": true',
)
_FALSEY_TOOL = frozenset({"", "false", "none", "null", "[]"})
_PATH_IN_TEXT = re.compile(
    r"([A-Za-z]:[\\/][^\s\"']+\.(?:png|jpe?g|webp|gif|dxf)|"
    r"/(?:[^\s\"']+/)*[^\s\"']+\.(?:png|jpe?g|webp|gif|dxf))",
    re.IGNORECASE,
)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return str(value)


as_text = _as_text


def _layout_prompt_json(state: dict[str, Any]) -> str:
    raw = state.get("layout") or state.get("priorLayout")
    if isinstance(raw, dict) and raw.get("kind") == "ev_charging_station_plan":
        from api.services.layouts.revise import compact_plan_for_prompt

        try:
            return compact_plan_for_prompt(raw)
        except Exception:  # noqa: BLE001
            return _as_text(raw)
    return _as_text(raw)


def _format_chunks_context(chunks: list[dict[str, Any]]) -> str:
    if not chunks:
        return "【历史案例参考】未命中。"
    parts: list[str] = [
        "【历史案例参考·禁止照抄数字】只借鉴布置手法（斜列/平行、沟截面、图例）。"
        "桩数、箱变台数与容量、场地长宽、出入口、建筑轮廓必须以【当前任务】和【读图】为准。"
    ]
    for i, c in enumerate(chunks[:8], start=1):
        score = float(c.get("score") or 0.0)
        name = str(c.get("name") or "").strip() or "未命名资料"
        raw = str(c.get("content") or "").strip()
        content = summarize_layout_case_for_prompt(raw, name=name)
        extra = f" 附图={len(c.get('images') or [])}" if c.get("images") else ""
        parts.append(f"[#{i} 资料={name} 相似度={score:.3f}{extra}]\n{content}")
    return "\n\n---\n\n".join(parts)


def _replace_templates(value: Any, state: dict[str, Any]) -> Any:
    """{{input}} / {{query}} / {{output}} / {{vision}} / {{layout}} / {{constraints}} 替换。"""
    mapping = {
        "{{input}}": _as_text(state.get("input")),
        "{{query}}": _as_text(state.get("query")),
        "{{output}}": _as_text(state.get("output")),
        "{{vision}}": _as_text(state.get("visionText")),
        "{{layout}}": _layout_prompt_json(state),
        "{{constraints}}": _as_text(state.get("constraints")),
        "{{lastSavedPath}}": _as_text(state.get("lastSavedPath")),
        "{{context}}": _as_text(state.get("context")),
    }
    if isinstance(value, str):
        out = value
        for k, v in mapping.items():
            out = out.replace(k, v)
        return out
    if isinstance(value, dict):
        return {k: _replace_templates(v, state) for k, v in value.items()}
    if isinstance(value, list):
        return [_replace_templates(v, state) for v in value]
    return value


def _bool_flag(data: dict[str, Any], *keys: str, default: bool = True) -> bool:
    raw: Any = None
    for k in keys:
        if k in data:
            raw = data.get(k)
            break
    if raw is None:
        return default
    if isinstance(raw, str):
        return raw.strip().lower() not in {"0", "false", "no", "off"}
    return bool(raw)


def _user_content(
    text: str, state: dict[str, Any], *, attach: bool, include_refs: bool = True
) -> Any:
    if not attach:
        return text
    user_parts = llm_image_parts(state.get("images") or [])
    ref_parts = llm_image_parts(state.get("refImages") or []) if include_refs else []
    parts = [*user_parts, *ref_parts]
    if not parts:
        return text
    caption = (text or "").strip() or DEFAULT_IMAGE_PROMPT
    if ref_parts:
        caption = (
            f"{caption}\n\n【历史案例附图】只借鉴布置模式，禁止描图、禁止照抄场地边界。"
        )
    return [{"type": "text", "text": caption}, *parts]


def _append_context(state: dict[str, Any], block: str) -> None:
    prev = _as_text(state.get("context")).strip()
    state["context"] = f"{prev}\n\n{block}".strip() if prev else block


def _append_output_image(state: dict[str, Any], mime: str, blob: bytes) -> None:
    imgs = list(state.get("outputImages") or [])
    if len(imgs) >= MAX_IMAGES:
        return
    imgs.extend(blobs_to_state_images([(mime, blob)]))
    state["outputImages"] = imgs[:MAX_IMAGES]


def _attach_saved_drawing_preview(state: dict[str, Any]) -> None:
    """CAD-MCP save_drawing 常写出 DXF；转成 PNG 才能进对话图库。"""
    path = str(state.get("lastSavedPath") or "").strip()
    if not path:
        return
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    try:
        if suffix in {"png", "jpg", "jpeg", "webp", "gif"}:
            mime, blob = read_local_image(path, allow_paths=[path])
            _append_output_image(state, mime, blob)
            return
        if is_cad_path(path):
            blob = rasterize_dxf(path)
            _append_output_image(state, "image/png", blob)
    except Exception:  # noqa: BLE001
        return


def _tool_result_ok(result: Any) -> bool:
    if not isinstance(result, dict):
        text = _as_text(result).strip().lower()
        return text not in _FALSEY_TOOL
    if result.get("isError") or result.get("error"):
        return False
    text = _as_text(result.get("content") if "content" in result else result).strip().lower()
    return text not in _FALSEY_TOOL


def _remember_saved_path(state: dict[str, Any], arguments: Any, result: Any) -> None:
    if not _tool_result_ok(result):
        return
    path = ""
    if isinstance(arguments, dict):
        path = str(
            arguments.get("file_path")
            or arguments.get("path")
            or arguments.get("filePath")
            or arguments.get("filename")
            or arguments.get("fileName")
            or ""
        ).strip()
    if not path:
        text = _as_text(result.get("content") if isinstance(result, dict) else result)
        m = _PATH_IN_TEXT.search(text)
        if m:
            path = m.group(1)
    if path:
        state["lastSavedPath"] = path


def eval_condition(data: dict[str, Any], state: dict[str, Any]) -> bool:
    """条件节点：hasImages / needImage / outputContains / toolFailed / alwaysTrue。"""
    kind = str(data.get("when") or data.get("kind") or "hasImages").strip()
    if kind == "hasImages":
        return bool(state.get("images"))
    if kind == "needImage":
        blob = (
            _as_text(state.get("output"))
            + "\n"
            + _as_text(state.get("visionText"))
            + "\n"
            + _as_text(state.get("query"))
        ).lower()
        return any(h in blob for h in _NEED_IMAGE_HINTS)
    if kind == "outputContains":
        needle = str(data.get("contains") or "").strip()
        if not needle:
            return False
        return needle.lower() in _as_text(state.get("output")).lower()
    if kind == "toolFailed":
        return not _tool_result_ok(state.get("lastToolResult"))
    if kind in {"alwaysTrue", "true"}:
        return True
    if kind in {"alwaysFalse", "false"}:
        return False
    return False


async def execute_node(
    db: AsyncSession,
    *,
    node: dict[str, Any],
    state: dict[str, Any],
    on_event: OnEvent | None = None,
) -> dict[str, Any]:
    """执行单节点并返回 detail 摘要（同时原地修改 state）。"""
    ntype = str(node.get("type") or "").strip()
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    if ntype == "start":
        return await _exec_start(state)
    if ntype == "knowledge":
        return await _exec_knowledge(db, data=data, state=state)
    if ntype == "llm":
        return await _exec_llm(db, data=data, state=state)
    if ntype == "agent":
        return await _exec_agent(db, data=data, state=state, on_event=on_event)
    if ntype == "mcp":
        return await _exec_mcp(db, data=data, state=state)
    if ntype == "condition":
        return await _exec_condition(data=data, state=state)
    if ntype == "vision":
        return await _exec_vision(db, data=data, state=state)
    if ntype == "image_out":
        return await _exec_image_out(db, data=data, state=state)
    if ntype == "layout_out":
        return await _exec_layout_out(data=data, state=state)
    if ntype == "end":
        return await _exec_end(state)
    raise AppError(
        ErrorCode.VALIDATION, f"unsupported node type: {ntype}", status_code=422
    )


async def _exec_start(state: dict[str, Any]) -> dict[str, Any]:
    raw = state.get("input")
    vars_map: dict[str, Any] = dict(state.get("vars") or {})
    if isinstance(raw, dict):
        query = _as_text(raw.get("query") or raw.get("text") or "")
        ctx = raw.get("contextText") or raw.get("context")
        if ctx is not None and not _as_text(state.get("context")).strip():
            state["context"] = _as_text(ctx)
        if raw.get("instruction"):
            vars_map["instruction"] = raw.get("instruction")
        if raw.get("systemHint"):
            vars_map["systemHint"] = raw.get("systemHint")
        if not state.get("images") and raw.get("images"):
            from api.services.ai.chat_images import ingest_input_images

            imgs, draft = ingest_input_images(raw.get("images"))
            state["images"] = imgs
            if draft:
                state["draftPlan"] = draft
    else:
        query = _as_text(raw)
    state["query"] = query
    state["vars"] = vars_map
    if not state.get("context"):
        state["context"] = ""
    if not isinstance(state.get("images"), list):
        state["images"] = []
    if not isinstance(state.get("refImages"), list):
        state["refImages"] = []
    if not isinstance(state.get("outputImages"), list):
        state["outputImages"] = []
    await _fill_chat_history(state)
    return {
        "query": query,
        "hasContext": bool(_as_text(state.get("context")).strip()),
        "imageCount": len(state.get("images") or []),
    }


async def _fill_chat_history(state: dict[str, Any]) -> None:
    if _as_text(state.get("chatHistory")).strip():
        return
    session = state.get("chatSession")
    if session is None:
        return
    try:
        from api.services.ai import memory as memory_svc

        msgs = await memory_svc.load_hot_messages(session.public_id)
    except Exception:
        return
    query = _as_text(state.get("query")).strip()
    lines: list[str] = []
    for item in msgs:
        role = str(item.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        text = str(item.get("content") or "").strip()
        if not text:
            continue
        if role == "assistant" and (
            "平面布置图" in text
            or "ev_charging_station_plan" in text
            or text.startswith("已生成")
        ):
            # 改参模式需要保留上一轮规模说明；几何已由 priorLayout 承接
            if not state.get("layoutRevise"):
                continue
            if len(text) > 240:
                text = text[:240] + "…"
        if len(text) > 700:
            text = text[:700] + "…"
        label = "用户" if role == "user" else "助手"
        lines.append(f"{label}：{text}")
    if lines and query and lines[-1].startswith("用户：") and query[:40] in lines[-1]:
        lines = lines[:-1]
    state["chatHistory"] = "\n".join(lines[-6:])


async def _exec_knowledge(
    db: AsyncSession, *, data: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    kb_ids_raw = data.get("knowledgeBaseIds") or data.get("knowledge_base_ids") or []
    kb_ids = [str(x).strip() for x in kb_ids_raw if str(x).strip()]
    top_k = int(data.get("topK") or data.get("top_k") or 3)
    top_k = max(1, min(top_k, 8))
    query = _as_text(state.get("query") or state.get("input")).strip()
    if not query:
        raise AppError(ErrorCode.VALIDATION, "knowledge node: empty query", status_code=422)
    if not kb_ids:
        raise AppError(
            ErrorCode.VALIDATION,
            "knowledge node: knowledgeBaseIds required",
            status_code=422,
        )
    max_drawings = data.get("maxDrawings")
    if max_drawings is None:
        max_drawings = data.get("max_drawings", 2)
    try:
        max_drawings_n = max(0, min(int(max_drawings), 2))
    except (TypeError, ValueError):
        max_drawings_n = 2
    docs = await search_knowledge_docs(
        db,
        query=query,
        top_k=top_k,
        min_score=0.0,
        kb_ids=kb_ids,
        max_drawings=max_drawings_n,
    )
    ctx = _format_chunks_context(docs)
    _append_context(state, ctx)
    state["refs"] = list(docs)
    ref_images: list[dict[str, Any]] = []
    cap = max(0, max_drawings_n)
    if cap:
        for item in docs:
            for img in item.get("images") or []:
                if isinstance(img, dict):
                    ref_images.append(img)
                if len(ref_images) >= cap:
                    break
            if len(ref_images) >= cap:
                break
    state["refImages"] = ref_images[:cap]
    return {
        "query": query,
        "topK": top_k,
        "refs": len(docs),
        "kbIds": kb_ids,
        "refImages": len(state["refImages"]),
    }


def _collect_system_parts(
    data: dict[str, Any],
    state: dict[str, Any],
    prompt_content: str | None,
    *,
    lock_prompt: bool = False,
) -> list[str]:
    parts: list[str] = []
    if prompt_content:
        parts.append(prompt_content)
    system_prompt = data.get("systemPrompt") or data.get("system_prompt")
    if system_prompt:
        raw = str(_replace_templates(system_prompt, state))
        if (
            not lock_prompt
            and "ev_charging_station_plan" in raw
            and "parkingRows" in raw
        ):
            from api.services.layouts.prompt import LAYOUT_LLM_SYSTEM_PROMPT

            raw = LAYOUT_LLM_SYSTEM_PROMPT
        parts.append(raw)
    vars_map = state.get("vars") if isinstance(state.get("vars"), dict) else {}
    system_hint = vars_map.get("systemHint")
    if system_hint:
        parts.append(str(system_hint))
    return parts


def _is_layout_prompt(parts: list[str]) -> bool:
    blob = "\n".join(parts)
    return "ev_charging_station_plan" in blob and "parkingRows" in blob


def _collect_user_text(
    state: dict[str, Any],
    *,
    extra: str | None = None,
    omit_rag: bool = False,
) -> str:
    if omit_rag:
        from api.services.layouts.prompt import build_layout_user_text
        from api.services.layouts.revise import (
            compact_plan_for_prompt,
            has_spatial_revise_intent,
        )

        prior = state.get("priorLayout")
        prior_json = ""
        if isinstance(prior, dict) and prior.get("kind") == "ev_charging_station_plan":
            prior_json = compact_plan_for_prompt(prior)
        query = _as_text(state.get("query") or state.get("input"))
        revise = bool(state.get("layoutRevise")) or (
            bool(prior_json) and has_spatial_revise_intent(query)
        )
        return build_layout_user_text(
            query=query,
            vision=_as_text(state.get("visionText")),
            history=_as_text(state.get("chatHistory")),
            has_style_image=bool(state.get("refImages")),
            prior_layout_json=prior_json,
            revise=revise,
        )
    user_parts: list[str] = []
    query = _as_text(state.get("query") or state.get("input")).strip()
    vision = _as_text(state.get("visionText")).strip()
    history = _as_text(state.get("chatHistory")).strip()
    context = _as_text(state.get("context")).strip()
    if query:
        user_parts.append(f"【当前任务·必须遵守】\n{query}")
    if vision:
        user_parts.append(f"【当前场地读图·以本次为准】\n{vision}")
    if history:
        user_parts.append(f"【本会话前文】场地与规模若与当前任务冲突，以当前任务和读图为准。\n{history}")
    if context:
        user_parts.append(context)
    vars_map = state.get("vars") if isinstance(state.get("vars"), dict) else {}
    instruction = vars_map.get("instruction")
    if instruction:
        user_parts.append(f"【生成指令】\n{instruction}")
    if extra:
        user_parts.append(extra)
    if not user_parts:
        user_parts.append(_as_text(state.get("input")) or "请继续")
    return "\n\n".join(user_parts)


async def _resolve_model_client(
    db: AsyncSession,
    data: dict[str, Any],
    *,
    state: dict[str, Any] | None = None,
    default_mode: str = "fast",
    require_vision: bool = False,
    allow_image_gen: bool = False,
):
    mode = str(data.get("mode") or default_mode).strip().lower()
    if mode not in {"fast", "deep"}:
        mode = "fast"
    model_id = data.get("modelId") or data.get("model_id")
    if not model_id and state:
        model_id = state.get("preferredModelId")
    model_id_s = str(model_id).strip() if model_id else None
    client = await build_llm_client(
        db,
        mode,
        model_id=model_id_s or None,
        require_vision=require_vision,
        allow_image_gen=allow_image_gen,
    )
    return client, mode, model_id_s


def _is_text_image_xor_error(exc: BaseException) -> bool:
    text = str(getattr(exc, "msg", None) or exc)
    return "not both" in text.lower() or "either 'text' or 'image'" in text.lower()


async def _complete_maybe_text_only(
    client: Any,
    messages: list[dict[str, Any]],
    *,
    mode: str,
    user_text: str,
) -> str:
    try:
        return await client.complete(messages, mode=mode)
    except AppError as exc:
        if not _is_text_image_xor_error(exc):
            raise
        fallback = list(messages)
        if fallback:
            last = dict(fallback[-1])
            last["content"] = user_text
            fallback[-1] = last
        return await client.complete(fallback, mode=mode)


def _save_llm_output(data: dict[str, Any], state: dict[str, Any], text: str) -> None:
    state["output"] = text
    save_as = str(data.get("saveAs") or data.get("save_as") or "").strip()
    if not save_as or not save_as.isidentifier():
        return
    if save_as in {"input", "query", "images", "outputImages", "chatSession"}:
        return
    if save_as == "constraints":
        from api.services.layouts.v2.constraints import (
            constraints_from_user_text,
            overlay_user_text_on_constraints,
            parse_constraints_text,
        )

        try:
            cons = parse_constraints_text(text)
        except Exception:  # noqa: BLE001
            cons = None
        if cons is None:
            fallback = constraints_from_user_text(_as_text(state.get("query")))
            if fallback is not None:
                state["constraints"] = fallback.model_dump(mode="json")
            else:
                state["constraints"] = text
            return
        from api.services.layouts.revise import parse_charger_type_hint

        asked_dc = parse_charger_type_hint(_as_text(state.get("query")))
        from api.services.layouts.revise import _parse_stall_range

        q = _as_text(state.get("query"))
        # 点名 27-50 号改功率时，不能让模型把整场 dcType 写成 320，否则出图又整场套同一桩型
        rng = _parse_stall_range(q)
        if asked_dc in {"dc_320kw", "dc_160kw", "dc_120kw"} and rng is None:
            cons.chargers.dcType = asked_dc  # type: ignore[assignment]
        elif rng is not None:
            cons.chargers.dcType = None
        has_prior = isinstance(state.get("priorLayout"), dict)
        cons = overlay_user_text_on_constraints(cons, q, overwrite_counts=not has_prior)
        state["constraints"] = cons.model_dump(mode="json")
        return
    state[save_as] = text


async def _exec_llm(
    db: AsyncSession, *, data: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    from api.services.layouts.revise import should_program_revise

    lock_prompt = _bool_flag(data, "lockPrompt", "lock_prompt", default=False)
    # 改图：不要让模型整案重画。v1 跳过全部 LLM；v2 仍抽条件表，但布置 JSON 用上一张。
    save_as = str(data.get("saveAs") or data.get("save_as") or "").strip()
    if should_program_revise(state) and (not lock_prompt or save_as != "constraints"):
        import json

        prior = state.get("priorLayout")
        state["output"] = json.dumps(prior, ensure_ascii=False)
        state["layoutRevise"] = True
        return {
            "mode": "revise",
            "skippedLlm": True,
            "reason": "program_revise",
            "chars": len(state["output"] or ""),
            "attachedImages": False,
        }

    prompt_id = data.get("promptId") or data.get("prompt_id")
    prompt_content = None
    if prompt_id:
        prompt_content = await prompt_configs.get_enabled_content(db, str(prompt_id).strip())
    system_parts = _collect_system_parts(
        data, state, prompt_content, lock_prompt=lock_prompt
    )
    layout_prompt = _is_layout_prompt(system_parts)
    # 出 JSON 必须能看本轮草稿。lockPrompt 只锁提示词，不能因此丢掉附图。
    # 仍用 deep 文本模型出 JSON，不因为附图去切视觉模型。
    if layout_prompt and state.get("images"):
        attach = True
        include_refs = False
    else:
        has_vision = bool(_as_text(state.get("visionText")).strip())
        attach = _bool_flag(data, "attachImages", "attach_images", default=True) and not has_vision
        include_refs = True
    need_vision = (
        attach
        and not layout_prompt
        and bool(state.get("images") or (state.get("refImages") if include_refs else []))
    )
    client, mode, model_id = await _resolve_model_client(
        db, data, state=state, require_vision=need_vision
    )
    user_template = data.get("userPrompt") or data.get("user_prompt")
    if user_template not in (None, ""):
        user_text = str(_replace_templates(str(user_template), state)).strip() or "请继续"
    else:
        user_text = _collect_user_text(state, omit_rag=layout_prompt)
    messages: list[dict[str, Any]] = []
    if system_parts:
        messages.append({"role": "system", "content": "\n\n".join(system_parts)})
    messages.append(
        {
            "role": "user",
            "content": _user_content(
                user_text, state, attach=attach, include_refs=include_refs
            ),
        }
    )
    text = await _complete_maybe_text_only(client, messages, mode=mode, user_text=user_text)
    _save_llm_output(data, state, text)
    return {
        "mode": mode,
        "modelId": model_id,
        "chars": len(text or ""),
        "promptId": prompt_id,
        "lockPrompt": lock_prompt,
        "saveAs": data.get("saveAs") or data.get("save_as"),
        "attachedImages": attach and bool(state.get("images") or state.get("refImages")),
    }


async def iter_agent_node(
    db: AsyncSession,
    *,
    data: dict[str, Any],
    state: dict[str, Any],
) -> AsyncIterator[dict[str, Any]]:
    """智能体节点：yield {kind:sse|detail}，不另开 asyncio.Task。"""
    agent_id = str(data.get("agentId") or data.get("agent_id") or "").strip()
    node_tool_ids = [
        str(x).strip()
        for x in (data.get("mcpToolIds") or data.get("mcp_tool_ids") or [])
        if str(x).strip()
    ]
    if agent_id:
        bundle = await agent_configs.get_enabled_bundle(db, agent_id)
    elif node_tool_ids:
        bundle = {
            "name": "节点内智能体",
            "mode": str(data.get("mode") or "fast"),
            "knowledgeBaseIds": list(data.get("knowledgeBaseIds") or []),
            "promptId": data.get("promptId") or data.get("prompt_id"),
            "toolsEnabled": True,
            "mcpToolIds": node_tool_ids,
        }
    else:
        raise AppError(
            ErrorCode.VALIDATION,
            "agent node: 请选择场景智能体，或勾选要循环调用的 MCP 工具",
            status_code=422,
        )
    mode = str(data.get("mode") or bundle.get("mode") or "fast")
    if mode not in {"fast", "deep"}:
        mode = "fast"
    query = _as_text(state.get("query") or state.get("input")).strip()
    kb_ids = list(bundle.get("knowledgeBaseIds") or [])
    refs: list[dict[str, Any]] = []
    if kb_ids and query:
        try:
            refs = await search_knowledge_docs(
                db, query=query, top_k=3, min_score=0.0, kb_ids=kb_ids, max_drawings=2
            )
        except Exception:  # noqa: BLE001
            refs = []
        if refs:
            _append_context(state, _format_chunks_context(refs))
            state["refs"] = refs
            ref_images: list[dict[str, Any]] = []
            for item in refs:
                for img in item.get("images") or []:
                    if isinstance(img, dict):
                        ref_images.append(img)
                    if len(ref_images) >= 2:
                        break
                if len(ref_images) >= 2:
                    break
            state["refImages"] = ref_images

    prompt_id = bundle.get("promptId")
    prompt_content = None
    if prompt_id:
        prompt_content = await prompt_configs.get_enabled_content(db, str(prompt_id))
    system_parts = _collect_system_parts(data, state, prompt_content)
    user_text = _collect_user_text(state)
    attach = _bool_flag(data, "attachImages", "attach_images", default=True)
    messages: list[dict[str, Any]] = []
    if system_parts:
        messages.append({"role": "system", "content": "\n\n".join(system_parts)})
    messages.append(
        {"role": "user", "content": _user_content(user_text, state, attach=attach)}
    )

    model_id = data.get("modelId") or data.get("model_id") or state.get("preferredModelId")
    client = await build_llm_client(
        db,
        mode,
        model_id=str(model_id).strip() if model_id else None,
        require_vision=attach and bool(state.get("images") or state.get("refImages")),
    )
    persist_hot = bool(state.get("persistHot") and state.get("chatSession"))
    session = state.get("chatSession") if persist_hot else None
    user_id = int(state.get("userId") or 0)
    tools_enabled = True if node_tool_ids else bool(bundle.get("toolsEnabled", True))
    mcp_ids = node_tool_ids or [
        str(x).strip()
        for x in (bundle.get("mcpToolIds") or [])
        if str(x).strip()
    ]
    if not tools_enabled:
        allowed: list[str] | None = []
    elif mcp_ids:
        allowed = mcp_ids
    else:
        allowed = None

    accumulated = ""
    last_tool: dict[str, Any] | None = None
    pending_args: dict[str, Any] = {}
    async for ev in run_chat_with_mcp(
        db,
        client=client,
        llm_messages=messages,
        mode=mode,
        session=session,
        user_id=user_id,
        kb_ids=kb_ids,
        chunks=refs,
        tools_enabled=tools_enabled,
        allowed_tool_ids=allowed,
        persist_hot=persist_hot,
    ):
        event = str(ev.get("event") or "")
        try:
            payload = json.loads(ev.get("data") or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if event == "delta":
            accumulated += str(payload.get("text") or "")
            yield {"kind": "sse", "event": "delta", "payload": payload}
        elif event == "tool":
            yield {"kind": "sse", "event": "tool", "payload": payload}
            if payload.get("phase") == "call" and isinstance(payload.get("arguments"), dict):
                pending_args = payload["arguments"]
            elif payload.get("phase") == "result":
                last_tool = payload
                _remember_saved_path(state, pending_args, payload)
        elif event == "error":
            yield {"kind": "sse", "event": "error", "payload": payload}
        elif event == "__final__":
            accumulated = str(payload.get("content") or accumulated)

    state["output"] = accumulated
    if last_tool is not None:
        state["lastToolResult"] = last_tool
    _attach_saved_drawing_preview(state)
    yield {
        "kind": "detail",
        "detail": {
            "agentId": agent_id,
            "agentName": bundle.get("name"),
            "mode": mode,
            "refs": len(refs),
            "chars": len(accumulated or ""),
            "toolsEnabled": tools_enabled,
            "attachedImages": attach and bool(state.get("images")),
            "outputImages": len(state.get("outputImages") or []),
            "lastSavedPath": state.get("lastSavedPath") or "",
        },
    }


async def _exec_agent(
    db: AsyncSession,
    *,
    data: dict[str, Any],
    state: dict[str, Any],
    on_event: OnEvent | None = None,
) -> dict[str, Any]:
    detail: dict[str, Any] = {}
    async for item in iter_agent_node(db, data=data, state=state):
        if item.get("kind") == "sse":
            if on_event:
                await on_event(str(item.get("event") or ""), dict(item.get("payload") or {}))
        elif item.get("kind") == "detail" and isinstance(item.get("detail"), dict):
            detail = item["detail"]
    return detail


async def _resolve_mcp_target(
    db: AsyncSession, data: dict[str, Any]
) -> tuple[str, str]:
    """返回 (server_public_id, tool_name)。"""
    tool_id = str(data.get("toolId") or data.get("tool_id") or "").strip()
    if tool_id:
        result = await db.execute(
            select(McpTool)
            .where(McpTool.public_id == tool_id)
            .options(selectinload(McpTool.server))
        )
        tool = result.scalar_one_or_none()
        if tool is None:
            raise AppError(ErrorCode.NOT_FOUND, "mcp tool not found", status_code=404)
        server = tool.server
        if server is None:
            server = await db.get(McpServer, tool.server_id)
        if server is None:
            raise AppError(ErrorCode.NOT_FOUND, "mcp server not found", status_code=404)
        return server.public_id, tool.name

    server_id = str(data.get("serverId") or data.get("server_id") or "").strip()
    tool_name = str(data.get("toolName") or data.get("tool_name") or "").strip()
    if not server_id or not tool_name:
        raise AppError(
            ErrorCode.VALIDATION,
            "mcp node: toolId or (serverId+toolName) required",
            status_code=422,
        )
    return server_id, tool_name


async def _exec_mcp(
    db: AsyncSession, *, data: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    server_id, tool_name = await _resolve_mcp_target(db, data)
    raw_args = data.get("arguments")
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args or "{}")
        except json.JSONDecodeError:
            raw_args = {}
    if not isinstance(raw_args, dict):
        raw_args = {}
    arguments = _replace_templates(raw_args, state)
    if not isinstance(arguments, dict):
        arguments = {}
    result = await mcp_servers.call_tool_on_server(
        db,
        server_public_id=server_id,
        tool_name=tool_name,
        arguments=arguments,
    )
    state["lastToolResult"] = result
    _remember_saved_path(state, arguments, result)
    _attach_saved_drawing_preview(state)
    text = _as_text(result.get("content") if isinstance(result, dict) else result)
    _append_context(state, f"【MCP {tool_name}】\n{text}")
    prev_out = _as_text(state.get("output")).strip()
    state["output"] = f"{prev_out}\n\n{text}".strip() if prev_out else text
    return {
        "serverId": server_id,
        "toolName": tool_name,
        "isError": bool(result.get("isError")) if isinstance(result, dict) else False,
        "preview": text[:500],
        "lastSavedPath": state.get("lastSavedPath") or "",
    }


async def _exec_condition(*, data: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    matched = eval_condition(data, state)
    state["lastCondition"] = matched
    return {
        "when": str(data.get("when") or "hasImages"),
        "matched": matched,
        "branch": "yes" if matched else "no",
    }


async def _exec_vision(
    db: AsyncSession, *, data: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    images = state.get("images") or []
    if not images:
        raise AppError(
            ErrorCode.VALIDATION,
            "读图节点需要输入图片（请在对话中上传，或从开始节点传入 images）",
            status_code=422,
        )
    client, mode, model_id = await _resolve_model_client(
        db, data, state=state, require_vision=True
    )
    prompt = str(
        data.get("prompt")
        or data.get("systemPrompt")
        or "请提取图中的尺寸、参数与要点，用简洁中文列出；若有表格请转成结构化文本。"
    )
    prompt = str(_replace_templates(prompt, state))
    query = _as_text(state.get("query")).strip()
    user_text = prompt if not query else f"{prompt}\n\n用户补充：{query}"
    user_text += (
        "\n必须给出 polygon: [{\"x\":..,\"y\":..}, ...]（至少 4 个顶点，米）。"
        "外形是 T 型/梯形时禁止只概括成长×宽矩形。"
        "出入口若画在场地某一角，必须写 corner=southeast/southwest/northeast/northwest"
        "（西南角原点、X东Y北），并写 along=east 或 west；禁止把角上的门写成南墙正中。"
        "若图上已画出充电车位/停车位，必须输出 parkingRows: "
        "[{\"id\":\"r1\",\"stalls\":8,\"stallWidthM\":3,\"stallLengthM\":6,\"angleDeg\":0,"
        "\"origin\":{\"x\":..,\"y\":..},\"along\":\"x\",\"charger\":{\"type\":\"none\",\"side\":\"head\"}}]；"
        "没有车位则 parkingRows: []。origin 为该排第一台西南角。"
    )
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": _user_content(user_text, state, attach=True, include_refs=False),
        }
    ]
    text = await _complete_maybe_text_only(client, messages, mode=mode, user_text=user_text)
    state["visionText"] = text
    _append_context(state, f"【读图】\n{text}")
    if _bool_flag(data, "copyToOutput", "copy_to_output", default=False):
        state["output"] = text
    return {
        "mode": mode,
        "modelId": model_id,
        "chars": len(text or ""),
        "imageCount": len(images),
    }


async def _exec_image_out(
    db: AsyncSession, *, data: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    mode_kind = str(data.get("mode") or "generate").strip().lower()
    if mode_kind in {"from_file", "file", "fromFile"}:
        path = str(
            _replace_templates(data.get("filePath") or "{{lastSavedPath}}", state)
        ).strip()
        if not path:
            text = _as_text(state.get("output")) + "\n" + _as_text(state.get("lastToolResult"))
            m = _PATH_IN_TEXT.search(text)
            path = m.group(1) if m else ""
        if not path:
            return {"mode": "from_file", "skipped": True, "reason": "无可用图片路径"}
        suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if is_cad_path(path) or suffix == "dxf":
            try:
                blob = rasterize_dxf(path)
            except Exception as exc:  # noqa: BLE001
                return {
                    "mode": "from_file",
                    "skipped": True,
                    "reason": f"DXF 转图片失败：{exc}",
                    "path": path,
                }
            _append_output_image(state, "image/png", blob)
            return {
                "mode": "from_file",
                "path": path,
                "mimeType": "image/png",
                "bytes": len(blob),
                "outputImages": len(state.get("outputImages") or []),
            }
        if suffix not in {"png", "jpg", "jpeg", "webp", "gif"}:
            return {
                "mode": "from_file",
                "skipped": True,
                "reason": f"路径不是图片或 DXF（.{suffix or '?'}）",
                "path": path,
            }
        allow = [str(state.get("lastSavedPath") or "")]
        mime, blob = read_local_image(path, allow_paths=allow)
        _append_output_image(state, mime, blob)
        return {
            "mode": "from_file",
            "path": path,
            "mimeType": mime,
            "bytes": len(blob),
            "outputImages": len(state.get("outputImages") or []),
        }

    prompt = str(
        data.get("prompt")
        or "根据以下说明生成配图，画面清晰、信息准确：\n{{output}}"
    )
    prompt = str(_replace_templates(prompt, state)).strip()
    if not prompt:
        prompt = _as_text(state.get("query") or "生成一张示意图")

    client, _, model_id = await _resolve_model_client(
        db, data, state=state, allow_image_gen=True
    )
    mime, blob = await client.generate_image(prompt)
    _append_output_image(state, mime, blob)
    return {
        "mode": "generate",
        "modelId": model_id,
        "mimeType": mime,
        "bytes": len(blob),
        "outputImages": len(state.get("outputImages") or []),
        "promptPreview": prompt[:200],
    }


async def _exec_layout_out(*, data: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    from api.services.layouts.pack import touch_up_plan
    from api.services.layouts.revise import (
        apply_spatial_hints,
        has_spatial_revise_intent,
        lock_site_geometry,
        preserve_parking_layout,
        revise_plan_from_prior,
    )
    from api.services.layouts.session_store import save_prior_layout

    source = data.get("jsonSource") or data.get("json_source") or "{{output}}"
    raw = _replace_templates(source, state)
    if isinstance(raw, dict):
        payload: Any = raw
    else:
        text = _as_text(raw).strip()
        if not text:
            existing = state.get("layout") or state.get("priorLayout")
            payload = existing if isinstance(existing, dict) else _as_text(state.get("output"))
        else:
            payload = text
    query = _as_text(state.get("query"))
    vision = _as_text(state.get("visionText"))
    from api.services.layouts.v2.constraints import (
        constraints_from_user_text,
        parse_constraints_any,
    )

    raw_cons = state.get("constraints")
    constraints = None
    if raw_cons not in (None, "", False, {}, []):
        constraints = parse_constraints_any(raw_cons)
        if constraints is None:
            raise AppError(
                ErrorCode.VALIDATION,
                "强制条件表无法解析，请检查「抽条件」节点输出",
                status_code=422,
            )
    if constraints is None:
        constraints = constraints_from_user_text(query, vision)
    if constraints is not None:
        from api.services.layouts.v2.pipeline import prepare_v2_plan

        if _bool_flag(data, "replaceOutputImages", "replace_output_images", default=False):
            state["outputImages"] = []
        prior_raw = state.get("priorLayout")
        spatial = has_spatial_revise_intent(query)
        from api.services.layouts.revise import parse_charger_type_hint

        param_revise = parse_charger_type_hint(query) is not None
        revise = isinstance(prior_raw, dict) and (
            bool(state.get("layoutRevise")) or spatial or param_revise
        )
        plan, issues = prepare_v2_plan(
            payload,
            query=query,
            vision=vision,
            constraints=constraints,
            prior=prior_raw if isinstance(prior_raw, dict) else None,
            revise=revise,
            draft=state.get("draftPlan"),
        )
    else:
        prior_raw = state.get("priorLayout")
        spatial = has_spatial_revise_intent(query)
        # 有上一张 +（显式修订标记 或 空间指令 或 改桩型）→ 走增量
        from api.services.layouts.revise import parse_charger_type_hint

        param_revise = parse_charger_type_hint(query) is not None
        revise = isinstance(prior_raw, dict) and (
            bool(state.get("layoutRevise")) or spatial or param_revise
        )

        llm_plan = None
        try:
            llm_plan = parse_plan(payload)
        except Exception:  # noqa: BLE001
            llm_plan = None

        if revise:
            prior = parse_plan(prior_raw)
            plan = revise_plan_from_prior(prior, query=query, llm_plan=llm_plan)
            # 改参不重新采轮廓；新场地请用户说「重新布置」并走非 revise 路径
        else:
            if llm_plan is None:
                if isinstance(prior_raw, dict):
                    plan = parse_plan(prior_raw)
                else:
                    plan = parse_plan(payload)
            else:
                plan = llm_plan
            plan = attach_site_polygon(plan, query, vision)
            plan = prune_unmentioned_site_context(plan, query, vision)

        brief = parse_layout_brief(query, vision)
        # 方案 A：brief + 目录尺寸强制覆盖，再确定性 pack
        plan = repair_plan(plan, brief)
        lock_env = bool(brief.site_w and brief.site_h)
        if revise and isinstance(prior_raw, dict):
            prior_plan = parse_plan(prior_raw)
            plan = lock_site_geometry(plan, prior_plan)
            # 有空间指令时不要把靠墙结果又还原成上一版原点
            if not spatial:
                plan = preserve_parking_layout(plan, prior_plan)
            plan = prepare_plan(plan, gentle=True, lock_envelope=lock_env, query=query)
            plan = apply_spatial_hints(plan, query)
            plan = touch_up_plan(plan, lock_envelope=lock_env)
        else:
            plan = prepare_plan(plan, lock_envelope=lock_env, query=query)
            # 即使没有 prior（或未标 revise），只要话里带靠墙/编号，也在当前方案上执行
            if spatial:
                plan = apply_spatial_hints(plan, query)
                plan = touch_up_plan(plan, lock_envelope=lock_env)
        issues = check_plan(plan, brief)
        blocking = [i for i in issues if i.blocking]
        if blocking:
            plan = repair_plan(plan, brief)
            if revise and isinstance(prior_raw, dict):
                prior_plan = parse_plan(prior_raw)
                plan = lock_site_geometry(plan, prior_plan)
                if not spatial:
                    plan = preserve_parking_layout(plan, prior_plan)
                plan = prepare_plan(plan, gentle=True, lock_envelope=lock_env, query=query)
                plan = apply_spatial_hints(plan, query)
                plan = touch_up_plan(plan, lock_envelope=lock_env)
            elif not revise:
                plan = attach_site_polygon(plan, query, vision)
                plan = prune_unmentioned_site_context(plan, query, vision)
                plan = repair_plan(plan, brief)
                plan = prepare_plan(plan, lock_envelope=lock_env, query=query)
                if spatial:
                    plan = apply_spatial_hints(plan, query)
                    plan = touch_up_plan(plan, lock_envelope=lock_env)
            issues = check_plan(plan, brief)
        notes = list(plan.notes or [])
        notes.extend(default_sheet_notes(query))
        notes.extend(issues_as_notes(issues))
        plan.notes = notes[:12]
    from api.services.layouts.pack import sync_charger_annotations
    from api.services.layouts.revise import apply_query_charger_to_plan

    apply_query_charger_to_plan(plan, query)
    sync_charger_annotations(plan)
    from api.services.layouts.cad_export.notes import ensure_cad_notes

    ensure_cad_notes(plan, query=query, constraints=constraints)
    dumped = plan.model_dump(mode="json")
    state["layout"] = dumped
    session = state.get("chatSession")
    sid = getattr(session, "public_id", None) if session is not None else None
    await save_prior_layout(sid, dumped)
    summary = plan_summary(plan)
    prior_for_diff = None
    prior_src = state.get("priorLayout")
    if isinstance(prior_src, dict) and prior_src.get("kind") == "ev_charging_station_plan":
        try:
            prior_for_diff = parse_plan(prior_src)
        except Exception:  # noqa: BLE001
            prior_for_diff = None
    from api.services.layouts.revise import describe_plan_changes

    change_lines = describe_plan_changes(prior_for_diff, plan, query=query)
    if change_lines:
        summary = summary + "\n" + "\n".join(change_lines)
    do_render = _bool_flag(data, "render", default=True)
    png_bytes = 0
    dxf_path = ""
    svg_path = ""
    layout_files: list[dict[str, str]] = []
    if do_render:
        from pathlib import Path

        from api.services.layouts.cad import export_plan_cad, write_plan_dxf
        from api.services.layouts.cad_export.files import layout_download_name, layout_stored_name
        from common.config import get_settings

        root = Path(get_settings().storage_root).expanduser().resolve() / "layouts"
        root.mkdir(parents=True, exist_ok=True)
        dxf_name = layout_stored_name(plan, ext="dxf")
        blob = b""
        try:
            saved, blob = export_plan_cad(plan, root / dxf_name, query=query)
            dxf_path = str(saved)
            state["lastSavedPath"] = dxf_path
            layout_files.append(
                {
                    "fileName": saved.name,
                    "downloadName": layout_download_name(plan, ext="dxf"),
                    "kind": "dxf",
                    "label": "CAD 总平面（DXF）",
                }
            )
            try:
                from api.services.layouts.cad_export.dwg import write_plan_dwg

                dwg = write_plan_dwg(saved)
            except Exception:  # noqa: BLE001
                dwg = None
            if dwg is not None:
                layout_files.append(
                    {
                        "fileName": dwg.name,
                        "downloadName": layout_download_name(plan, ext="dwg"),
                        "kind": "dwg",
                        "label": "CAD 总平面（DWG）",
                    }
                )
        except Exception:  # noqa: BLE001
            dxf_path = ""
            try:
                blob = render_plan_png(plan, query=query)
            except Exception:  # noqa: BLE001
                blob = b""
            try:
                saved = write_plan_dxf(plan, root / dxf_name)
                dxf_path = str(saved)
                state["lastSavedPath"] = dxf_path
                layout_files.append(
                    {
                        "fileName": saved.name,
                        "downloadName": layout_download_name(plan, ext="dxf"),
                        "kind": "dxf",
                        "label": "CAD 总平面（DXF）",
                    }
                )
                try:
                    from api.services.layouts.cad_export.dwg import write_plan_dwg

                    dwg = write_plan_dwg(saved)
                except Exception:  # noqa: BLE001
                    dwg = None
                if dwg is not None:
                    layout_files.append(
                        {
                            "fileName": dwg.name,
                            "downloadName": layout_download_name(plan, ext="dwg"),
                            "kind": "dwg",
                            "label": "CAD 总平面（DWG）",
                        }
                    )
            except Exception:  # noqa: BLE001
                dxf_path = ""
        if blob:
            _append_output_image(state, "image/png", blob)
            png_bytes = len(blob)
        try:
            from pathlib import Path

            from common.config import get_settings

            if layout_files and layout_files[0].get("kind") == "dxf":
                stem = Path(layout_files[0]["fileName"]).stem
                root = Path(get_settings().storage_root).expanduser().resolve() / "layouts"
                svg_saved = write_plan_svg(plan, root / f"{stem}.svg", query=query)
            else:
                svg_saved = write_plan_svg(plan, query=query)
            svg_path = str(svg_saved)
            state["lastSvgPath"] = svg_path
            layout_files.append(
                {
                    "fileName": svg_saved.name,
                    "downloadName": layout_download_name(plan, ext="svg"),
                    "kind": "svg",
                    "label": "SVG 矢量图",
                }
            )
        except Exception:  # noqa: BLE001
            svg_path = ""
        state["layoutFiles"] = layout_files
    if _bool_flag(data, "copyToOutput", "copy_to_output", default=True):
        text = summary
        if constraints is not None:
            extra = issues_as_notes(issues)
            if extra:
                text = text + "\n" + "\n".join(extra)
        state["output"] = text
    return {
        "title": plan.titleBlock.title,
        "stalls": sum(r.stalls for r in plan.parkingRows),
        "equipment": len(plan.equipment),
        "rendered": do_render,
        "bytes": png_bytes,
        "path": dxf_path,
        "dxfPath": dxf_path,
        "svgPath": svg_path,
        "layoutFiles": layout_files,
        "revise": revise,
        "spatial": spatial,
        "programRevise": revise,
        "outputImages": len(state.get("outputImages") or []),
        "summary": summary,
        "checkIssues": [item.message for item in issues],
        "issues": [i.code for i in issues],
    }


async def _exec_end(state: dict[str, Any]) -> dict[str, Any]:
    out = state.get("output")
    if out is None or (isinstance(out, str) and not out.strip()):
        if _as_text(state.get("visionText")).strip():
            state["output"] = state.get("visionText")
        elif state.get("lastToolResult") is not None:
            state["output"] = state.get("lastToolResult")
        elif _as_text(state.get("query")).strip():
            state["output"] = state.get("query")
        else:
            state["output"] = state.get("context") or state.get("input")
    return {
        "outputPreview": _as_text(state.get("output"))[:500],
        "outputImages": len(state.get("outputImages") or []),
    }
