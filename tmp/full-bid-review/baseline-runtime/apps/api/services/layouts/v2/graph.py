"""充电站布置 v2 默认图：检索案例 → 读图 → 抽条件 → JSON → 出图 → 校验失败则修补一次。"""

from __future__ import annotations

from typing import Any

from api.services.layouts.v2.prompts import (
    EXTRACT_SYSTEM,
    EXTRACT_USER,
    PLAN_SYSTEM,
    PLAN_USER,
    REPAIR_SYSTEM,
    REPAIR_USER,
    VISION_PROMPT,
)

WORKFLOW_NAME = "充电站布置 v2"
WORKFLOW_REMARK = "DeepSeek 抽条件+出JSON，Qwen 只读图，程序 layout_out 出图"
KB_NAME = "充电站布置案例"
# 旧种子「充电站平面布置」已由本工作流替代，seed 会按名删除，勿再发布同名图。
RETIRED_WORKFLOW_NAME = "充电站平面布置"


def default_layout_v2_graph(
    *, knowledge_base_ids: list[str] | None = None
) -> dict[str, Any]:
    kb_ids = [str(x).strip() for x in (knowledge_base_ids or []) if str(x).strip()]
    nodes: list[dict[str, Any]] = [
        {
            "id": "start",
            "type": "start",
            "position": {"x": 40, "y": 260},
            "data": {},
        }
    ]
    edges: list[dict[str, Any]] = []
    if kb_ids:
        nodes.append(
            {
                "id": "knowledge",
                "type": "knowledge",
                "position": {"x": 220, "y": 240},
                "data": {"knowledgeBaseIds": kb_ids, "topK": 2, "maxDrawings": 0},
            }
        )
        edges.append({"id": "e_start_kb", "source": "start", "target": "knowledge"})
        cond_source = "knowledge"
        cond_x = 430
        edges.append({"id": "e_kb_cond", "source": cond_source, "target": "cond_img"})
    else:
        edges.append({"id": "e_start_cond", "source": "start", "target": "cond_img"})
        cond_x = 220

    lock = {
        "lockPrompt": True,
        "attachImages": False,
        "mode": "deep",
    }
    nodes.extend(
        [
            {
                "id": "cond_img",
                "type": "condition",
                "position": {"x": cond_x, "y": 240},
                "data": {"when": "hasImages"},
            },
            {
                "id": "vision",
                "type": "vision",
                "position": {"x": cond_x + 220, "y": 80},
                "data": {"prompt": VISION_PROMPT, "copyToOutput": False},
            },
            {
                "id": "extract",
                "type": "llm",
                "position": {"x": cond_x + 440, "y": 240},
                "data": {
                    **lock,
                    "saveAs": "constraints",
                    "systemPrompt": EXTRACT_SYSTEM,
                    "userPrompt": EXTRACT_USER,
                },
            },
            {
                "id": "plan",
                "type": "llm",
                "position": {"x": cond_x + 680, "y": 240},
                "data": {
                    **lock,
                    "systemPrompt": PLAN_SYSTEM,
                    "userPrompt": PLAN_USER,
                },
            },
            {
                "id": "draw1",
                "type": "layout_out",
                "position": {"x": cond_x + 920, "y": 240},
                "data": {
                    "jsonSource": "{{output}}",
                    "render": True,
                    "copyToOutput": True,
                },
            },
            {
                "id": "cond_chk",
                "type": "condition",
                "position": {"x": cond_x + 1160, "y": 240},
                "data": {"when": "outputContains", "contains": "校验未通过"},
            },
            {
                "id": "fix",
                "type": "llm",
                "position": {"x": cond_x + 1160, "y": 80},
                "data": {
                    **lock,
                    "systemPrompt": REPAIR_SYSTEM,
                    "userPrompt": REPAIR_USER,
                },
            },
            {
                "id": "draw2",
                "type": "layout_out",
                "position": {"x": cond_x + 1400, "y": 80},
                "data": {
                    "jsonSource": "{{output}}",
                    "render": True,
                    "copyToOutput": True,
                    "replaceOutputImages": True,
                },
            },
            {
                "id": "end",
                "type": "end",
                "position": {"x": cond_x + 1400, "y": 240},
                "data": {},
            },
        ]
    )
    edges.extend(
        [
            {
                "id": "e_cond_vision",
                "source": "cond_img",
                "target": "vision",
                "sourceHandle": "yes",
            },
            {
                "id": "e_cond_extract",
                "source": "cond_img",
                "target": "extract",
                "sourceHandle": "no",
            },
            {"id": "e_vision_extract", "source": "vision", "target": "extract"},
            {"id": "e_extract_plan", "source": "extract", "target": "plan"},
            {"id": "e_plan_draw1", "source": "plan", "target": "draw1"},
            {"id": "e_draw1_chk", "source": "draw1", "target": "cond_chk"},
            {
                "id": "e_chk_fix",
                "source": "cond_chk",
                "target": "fix",
                "sourceHandle": "yes",
            },
            {
                "id": "e_chk_end",
                "source": "cond_chk",
                "target": "end",
                "sourceHandle": "no",
            },
            {"id": "e_fix_draw2", "source": "fix", "target": "draw2"},
            {"id": "e_draw2_end", "source": "draw2", "target": "end"},
        ]
    )
    return {"nodes": nodes, "edges": edges}
