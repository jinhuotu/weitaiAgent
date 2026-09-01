"""工作流图校验、条件分支与模板替换（无 DB）。"""

from __future__ import annotations

import pytest

from common.errors import AppError
from api.services.workflows.crud import validate_graph
from api.services.workflows.nodes import _replace_templates, eval_condition
from api.services.workflows.runner import pick_next_node_id


def test_validate_graph_accepts_new_node_types() -> None:
    graph = {
        "nodes": [
            {"id": "start", "type": "start", "data": {}},
            {"id": "cond", "type": "condition", "data": {"when": "hasImages"}},
            {"id": "vision", "type": "vision", "data": {}},
            {"id": "img", "type": "image_out", "data": {"mode": "generate"}},
            {"id": "layout", "type": "layout_out", "data": {}},
            {"id": "end", "type": "end", "data": {}},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "cond"},
            {
                "id": "e2",
                "source": "cond",
                "target": "vision",
                "sourceHandle": "yes",
            },
            {
                "id": "e3",
                "source": "cond",
                "target": "img",
                "sourceHandle": "no",
            },
            {"id": "e4", "source": "vision", "target": "end"},
            {"id": "e5", "source": "img", "target": "end"},
            {"id": "e6", "source": "layout", "target": "end"},
        ],
    }
    out = validate_graph(graph)
    types = {n["id"]: n["type"] for n in out["nodes"]}
    assert types["cond"] == "condition"
    assert types["vision"] == "vision"
    assert types["layout"] == "layout_out"
    handles = {e["id"]: e.get("sourceHandle") for e in out["edges"]}
    assert handles["e2"] == "yes"
    assert handles["e3"] == "no"


def test_eval_condition_has_images_and_contains() -> None:
    assert eval_condition({"when": "hasImages"}, {"images": [{"mimeType": "image/png"}]})
    assert not eval_condition({"when": "hasImages"}, {"images": []})
    assert eval_condition(
        {"when": "outputContains", "contains": "完成"},
        {"output": "图纸已完成"},
    )
    assert eval_condition({"when": "needImage"}, {"output": "请生成图片"})
    assert eval_condition({"when": "toolFailed"}, {"lastToolResult": {"content": "False"}})
    assert not eval_condition(
        {"when": "toolFailed"}, {"lastToolResult": {"content": "ok", "isError": False}}
    )


def test_pick_next_condition_yes_no() -> None:
    node = {"id": "c1", "type": "condition"}
    edges = [
        {"source": "c1", "target": "a", "sourceHandle": "yes"},
        {"source": "c1", "target": "b", "sourceHandle": "no"},
    ]
    assert pick_next_node_id(node, edges, {"lastCondition": True}) == "a"
    assert pick_next_node_id(node, edges, {"lastCondition": False}) == "b"


def test_pick_next_missing_branch_raises() -> None:
    node = {"id": "c1", "type": "condition"}
    edges = [{"source": "c1", "target": "a", "sourceHandle": "yes"}]
    with pytest.raises(AppError) as ei:
        pick_next_node_id(node, edges, {"lastCondition": False})
    assert "no" in str(ei.value.msg)


def test_replace_templates_vision_and_path() -> None:
    out = _replace_templates(
        "v={{vision}} p={{lastSavedPath}}",
        {"visionText": "孔距 20", "lastSavedPath": r"E:\downLoad\a.png"},
    )
    assert "孔距 20" in out
    assert r"E:\downLoad\a.png" in out
