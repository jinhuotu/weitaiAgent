"""充电站布置 v2：条件表、图结构、引擎开关。"""

from __future__ import annotations

import json
import math

import pytest

from api.services.layouts.schema import EXAMPLE_PLAN
from api.services.layouts.v2.constraints import (
    LayoutConstraints,
    check_constraints,
    overlay_user_text_on_constraints,
    parse_constraints_text,
    seed_plan_payload,
    wanted_stalls,
)
from api.services.layouts.v2.graph import (
    RETIRED_WORKFLOW_NAME,
    WORKFLOW_NAME,
    default_layout_v2_graph,
)
from api.services.layouts.v2.pipeline import prepare_v2_plan
from api.services.layouts.v2.prompts import PLAN_SYSTEM
from api.services.workflows.crud import validate_graph
from api.services.workflows.nodes import (
    _collect_system_parts,
    _exec_layout_out,
    _replace_templates,
    _save_llm_output,
)


def test_parse_constraints_from_user_turn() -> None:
    raw = """```json
    {"schemaVersion":"1","kind":"ev_charging_station_constraints",
     "fleet":{"cars":15,"trucks":0,"piles":15},
     "transformers":[{"count":3,"kva":1600}],
     "layout":{"mode":"双排斜列","angleDeg":-45,"wallSide":"north"}}
    ```"""
    cons = parse_constraints_text(raw)
    assert cons.fleet.cars == 15
    assert cons.layout.mode == "dual_row_angle"
    assert cons.layout.wallSide == "north"
    assert wanted_stalls(cons) == (15, 0)


def test_check_constraints_flags_wrong_count() -> None:
    cons = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": 15, "trucks": 0, "piles": 15},
            "transformers": [{"count": 1, "kva": 1600}],
            "layout": {"angleDeg": -45},
        }
    )
    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["parkingRows"] = [
        {
            "id": "cars",
            "stalls": 8,
            "stallWidthM": 3,
            "stallLengthM": 6,
            "angleDeg": 0,
            "origin": {"x": 4, "y": 6},
            "along": "x",
            "charger": {"type": "dc_160kw", "startNo": 1, "side": "head"},
        }
    ]
    from api.services.layouts.parse import parse_plan

    issues = check_constraints(parse_plan(payload), cons)
    codes = {i.code for i in issues}
    assert "car_count" in codes or "pile_count" in codes or "stall_total" in codes
    assert "angle" in codes


def test_v2_replaces_retired_v1_name() -> None:
    assert WORKFLOW_NAME == "充电站布置 v2"
    assert RETIRED_WORKFLOW_NAME == "充电站平面布置"
    assert WORKFLOW_NAME != RETIRED_WORKFLOW_NAME


def test_v2_graph_valid() -> None:
    out = validate_graph(default_layout_v2_graph(knowledge_base_ids=["kb1"]))
    types = {n["id"]: n["type"] for n in out["nodes"]}
    assert types["extract"] == "llm"
    assert types["plan"] == "llm"
    assert types["draw1"] == "layout_out"
    assert types["draw2"] == "layout_out"
    extract = next(n for n in out["nodes"] if n["id"] == "extract")
    assert extract["data"].get("lockPrompt") is True
    assert extract["data"].get("saveAs") == "constraints"
    plan = next(n for n in out["nodes"] if n["id"] == "plan")
    assert plan["data"].get("lockPrompt") is True
    handles = {e["id"]: e.get("sourceHandle") for e in out["edges"] if e.get("sourceHandle")}
    assert handles["e_cond_vision"] == "yes"
    assert handles["e_chk_fix"] == "yes"
    assert handles["e_chk_end"] == "no"


def test_lock_prompt_skips_old_layout_rewrite() -> None:
    parts = _collect_system_parts(
        {"systemPrompt": PLAN_SYSTEM},
        {},
        None,
        lock_prompt=True,
    )
    blob = "\n".join(parts)
    assert "强制条件表" in blob
    assert "禁止抄这里的桩数" in blob
    unlocked = _collect_system_parts(
        {"systemPrompt": PLAN_SYSTEM},
        {},
        None,
        lock_prompt=False,
    )
    assert "职责边界" in "\n".join(unlocked)


def test_constraints_template_and_save_as() -> None:
    state: dict = {}
    _save_llm_output(
        {"saveAs": "constraints"},
        state,
        json.dumps(
            {
                "kind": "ev_charging_station_constraints",
                "fleet": {"piles": 12, "cars": 12, "trucks": 0},
            },
            ensure_ascii=False,
        ),
    )
    assert state["constraints"]["fleet"]["piles"] == 12
    text = _replace_templates("条件={{constraints}}", state)
    assert "12" in text


def test_save_constraints_overlays_user_text_when_json_empty() -> None:
    state = {"query": "场地约5000㎡，布置50台160kW充电桩，两台箱变"}
    _save_llm_output(
        {"saveAs": "constraints"},
        state,
        json.dumps(
            {
                "kind": "ev_charging_station_constraints",
                "fleet": {"cars": None, "trucks": None, "piles": None},
                "chargers": {"dcType": None},
                "transformers": [],
                "site": {"areaM2": None},
            }
        ),
    )
    cons = state["constraints"]
    assert cons["fleet"]["cars"] == 50
    assert cons["chargers"]["dcType"] == "dc_160kw"
    assert cons["transformers"][0]["count"] == 2
    assert cons["site"]["areaM2"] == 5000


def test_save_constraints_overwrites_copied_eight_stalls() -> None:
    state = {"query": "50台160kW充电桩"}
    _save_llm_output(
        {"saveAs": "constraints"},
        state,
        json.dumps(
            {
                "kind": "ev_charging_station_constraints",
                "fleet": {"cars": 8, "trucks": 0, "piles": 8},
                "chargers": {"dcType": "dc_160kw"},
            }
        ),
    )
    assert state["constraints"]["fleet"]["cars"] == 50
    assert state["constraints"]["fleet"]["piles"] == 50


def test_prepare_v2_plan_enforces_catalog_and_count() -> None:
    cons = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": 8, "trucks": 0, "piles": 8},
            "transformers": [{"count": 1, "kva": 1250}],
            "site": {"gates": [{"side": "south"}]},
        }
    )
    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["parkingRows"] = [
        {
            "id": "cars",
            "stalls": 8,
            "stallWidthM": 2.4,
            "stallLengthM": 5.5,
            "angleDeg": -45,
            "origin": {"x": 6, "y": 8},
            "along": "x",
            "charger": {"type": "dc_160kw", "startNo": 1, "side": "head"},
        }
    ]
    payload["equipment"] = [
        {
            "id": "tx1",
            "type": "box_transformer",
            "x": 34,
            "y": 22,
            "label": "1250kVA箱变",
            "capacityKva": 1250,
        }
    ]
    plan, issues = prepare_v2_plan(
        payload, query="8个160kW充电桩，1台1250kVA箱变", vision="", constraints=cons
    )
    assert plan.parkingRows[0].stallWidthM == 3.0
    assert plan.parkingRows[0].stallLengthM == 6.0
    blocking = [i for i in issues if i.blocking]
    assert not blocking


def test_v2_pins_rows_to_walls_and_feeders_near_piles() -> None:
    """20×30、南门、两排漂在场中：应变为东西靠墙，箱变贴在对应桩排端头。"""
    from api.services.layouts.pack import _eq_box, _overlap, _row_aabb, charger_heads

    cons = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": 9, "trucks": 0, "piles": 9},
            "transformers": [{"count": 2, "kva": 2000}],
            "site": {
                "widthM": 20,
                "heightM": 30,
                "gates": [{"side": "south", "widthM": 8}],
            },
            "layout": {"mode": "parallel", "angleDeg": 0},
        }
    )
    payload = {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {
            "widthM": 20,
            "heightM": 30,
            "gate": {"side": "south", "offsetM": 6, "widthM": 8, "label": "出入口"},
        },
        "buildings": [],
        "roads": [],
        "parkingRows": [
            {
                "id": "a",
                "stalls": 5,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "angleDeg": 0,
                "origin": {"x": 4, "y": 8},
                "along": "x",
                "charger": {"type": "dc_160kw", "startNo": 1, "side": "head"},
            },
            {
                "id": "b",
                "stalls": 4,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "angleDeg": 0,
                "origin": {"x": 4, "y": 16},
                "along": "x",
                "charger": {"type": "dc_160kw", "startNo": 6, "side": "head"},
            },
        ],
        "equipment": [
            {
                "id": "tx1",
                "type": "box_transformer",
                "x": 10,
                "y": 26,
                "label": "2000kVA箱变",
                "capacityKva": 2000,
            },
            {
                "id": "tx2",
                "type": "box_transformer",
                "x": 16,
                "y": 15,
                "label": "2000kVA箱变",
                "capacityKva": 2000,
            },
        ],
    }
    plan, issues = prepare_v2_plan(
        payload,
        query="20m×30m场地 9个160kW充电桩 两台2000kVA箱变 南侧出入口",
        vision="",
        constraints=cons,
    )
    assert sum(r.stalls for r in plan.parkingRows) == 9
    boxes = [_row_aabb(r) for r in plan.parkingRows]
    westish = sum(1 for b in boxes if b[0] <= 1.6)
    eastish = sum(1 for b in boxes if b[2] >= 20 - 1.6)
    assert westish >= 1 and eastish >= 1, "两排应分列东西墙"
    mid_x = [((b[0] + b[2]) / 2) for b in boxes]
    assert not all(7 < x < 13 for x in mid_x), "车位不得都留在场地中央"
    aisles = plan.aisles
    assert aisles, "应保留行车通道"
    _assert_stalls_aligned_and_disjoint(plan)
    feeders = [eq for eq in plan.equipment if "transformer" in eq.type]
    assert len(feeders) == 2
    for eq in feeders:
        eb = _eq_box(eq.x, eq.y)
        for row in plan.parkingRows:
            assert not _overlap(eb, _row_aabb(row), pad=-0.2)
        assert eq.y >= 8.0, "南侧出入口前方不得放箱变"
        nearest = min(
            math.hypot(eq.x - hx, eq.y - hy)
            for row in plan.parkingRows
            for hx, hy in charger_heads(row)
        )
        assert nearest < 14.0, "箱变应靠近所连充电桩"
    _assert_stalls_aligned_and_disjoint(plan)


def _assert_stalls_aligned_and_disjoint(plan) -> None:
    from api.services.layouts.pack import (
        _overlap,
        _point_in_poly,
        charger_heads,
        charger_outset_m,
        charger_point,
        expand_stalls,
        rect_corners,
    )

    boxes = []
    for row in plan.parkingRows:
        heads = charger_heads(row)
        stalls = expand_stalls(row)
        assert not heads or len(heads) == len(stalls)
        outset = charger_outset_m(row)
        for idx, (rect, _) in enumerate(stalls):
            boxes.append(_aabb_of(rect))
            if row.along == "y" and abs(float(row.angleDeg)) < 15:
                assert rect.w > rect.h + 0.5, "东西墙必须对墙竖放（进深=车长，沿墙=车宽）"
            if row.charger and row.charger.type != "none":
                hx, hy = charger_point(rect, row.charger.side, outset=outset)
                poly = rect_corners(rect)
                assert not _point_in_poly(hx, hy, poly), "充电桩必须在车位轮廓外对齐，不能压在车位中间"
                if row.charger.side == "left":
                    assert hx <= rect.x + 0.05, "西墙车头应对准充电桩，不能车身对桩"
                elif row.charger.side == "right":
                    assert hx >= rect.x + rect.w - 0.05, "东墙车头应对准充电桩，不能车身对桩"
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert not _overlap(a, b, pad=0.05), "车位不得重叠"


def _aabb_of(rect) -> tuple[float, float, float, float]:
    from api.services.layouts.pack import _aabb, rect_corners

    return _aabb(rect_corners(rect))


def test_along_y_parallel_stalls_do_not_overlap() -> None:
    from api.services.layouts.pack import _aabb, _overlap, expand_stalls, rect_corners
    from api.services.layouts.schema import ChargerOnRow, ParkingRowSpec, PointM

    row = ParkingRowSpec(
        id="west",
        stalls=4,
        stallWidthM=3,
        stallLengthM=6,
        angleDeg=0,
        origin=PointM(x=0.8, y=4),
        along="y",
        charger=ChargerOnRow(type="dc_160kw", startNo=1, side="left"),
    )
    boxes = [_aabb(rect_corners(rect)) for rect, _ in expand_stalls(row)]
    assert len(boxes) == 4
    for i, a in enumerate(boxes):
        assert a[2] - a[0] == pytest.approx(6.0, abs=0.05)
        assert a[3] - a[1] == pytest.approx(3.0, abs=0.05)
        for b in boxes[i + 1 :]:
            assert not _overlap(a, b, pad=0.05)
    from api.services.layouts.pack import row_pitch

    assert row_pitch(row) >= 3.8


@pytest.mark.asyncio
async def test_v2_layout_out_uses_constraints_and_writes_check_notes() -> None:
    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["site"] = {
        "widthM": 12,
        "heightM": 10,
        "northDeg": 0,
        "gate": {"side": "south", "offsetM": 2, "widthM": 4, "label": "出入口"},
        "polygon": [
            {"x": 0, "y": 0},
            {"x": 12, "y": 0},
            {"x": 12, "y": 10},
            {"x": 0, "y": 10},
        ],
    }
    payload["buildings"] = []
    payload["roads"] = []
    payload["parkingRows"] = [
        {
            "id": "cars",
            "stalls": 20,
            "stallWidthM": 3,
            "stallLengthM": 6,
            "angleDeg": 0,
            "origin": {"x": 1, "y": 1},
            "along": "x",
            "charger": {"type": "dc_160kw", "startNo": 1, "side": "head"},
        }
    ]
    cons = {
        "kind": "ev_charging_station_constraints",
        "fleet": {"cars": 20, "trucks": 0, "piles": 20},
        "site": {"widthM": 12, "heightM": 10, "gates": [{"side": "south"}]},
    }
    state = {
        "query": "12m×10m场地放20个充电桩",
        "output": json.dumps(payload, ensure_ascii=False),
        "constraints": cons,
        "outputImages": [],
    }
    detail = await _exec_layout_out(
        data={"render": False, "copyToOutput": True}, state=state
    )
    assert detail.get("programRevise") is False
    assert "校验未通过" in str(state["output"])


def test_v2_followup_keeps_prior_site_and_four_plus_four() -> None:
    """只改东南角出入口时：长宽、4+4 两侧排列不得被重画。"""
    from api.services.layouts.pack import _overlap, _row_aabb
    from api.services.layouts.schema import collect_site_gates

    cons = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": 12, "trucks": 0, "piles": 12},
            "site": {"widthM": 30, "heightM": 24, "gates": [{"side": "east"}]},
            "layout": {"wallSide": "east"},
        }
    )
    prior = {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {
            "widthM": 20,
            "heightM": 30,
            "gate": {"side": "south", "offsetM": 6, "widthM": 8, "label": "出入口"},
        },
        "buildings": [],
        "roads": [],
        "parkingRows": [
            {
                "id": "a",
                "stalls": 4,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "angleDeg": 0,
                "origin": {"x": 0.8, "y": 8},
                "along": "y",
                "charger": {"type": "dc_160kw", "startNo": 1, "side": "left"},
            },
            {
                "id": "b",
                "stalls": 4,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "angleDeg": 0,
                "origin": {"x": 13.2, "y": 8},
                "along": "y",
                "charger": {"type": "dc_160kw", "startNo": 5, "side": "right"},
            },
        ],
        "equipment": [
            {"id": "tx1", "type": "box_transformer", "x": 2.4, "y": 26, "capacityKva": 2000},
            {"id": "tx2", "type": "box_transformer", "x": 17.6, "y": 26, "capacityKva": 2000},
        ],
    }
    bogus = json.loads(json.dumps(prior))
    bogus["site"]["widthM"] = 30
    bogus["site"]["heightM"] = 24
    bogus["parkingRows"][0]["stalls"] = 6
    bogus["parkingRows"][1]["stalls"] = 6
    bogus["parkingRows"][1]["along"] = "x"
    plan, _issues = prepare_v2_plan(
        bogus,
        query="出入口在东南角 请你帮我修改一下",
        vision="",
        constraints=cons,
        prior=prior,
        revise=True,
    )
    assert plan.site.widthM == 20
    assert plan.site.heightM == 30
    assert sum(r.stalls for r in plan.parkingRows) == 8
    assert [r.stalls for r in plan.parkingRows] == [4, 4]
    gates = collect_site_gates(plan.site)
    assert gates and gates[0].side == "south"
    assert gates[0].offsetM > 8.0
    boxes = [_row_aabb(r) for r in plan.parkingRows]
    westish = sum(1 for b in boxes if b[0] <= 1.6)
    eastish = sum(1 for b in boxes if b[2] >= 20 - 1.6)
    assert westish >= 1 and eastish >= 1
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert not _overlap(a, b, pad=0.05)
    _assert_stalls_aligned_and_disjoint(plan)


def _dual16_payload() -> dict:
    return {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {
            "widthM": 31,
            "heightM": 34,
            "gate": {"side": "south", "offsetM": 11, "widthM": 8, "label": "出入口"},
        },
        "buildings": [],
        "roads": [],
        "parkingRows": [
            {
                "id": "a",
                "stalls": 8,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "angleDeg": 0,
                "origin": {"x": 0.8, "y": 4},
                "along": "y",
                "charger": {"type": "dc_160kw", "startNo": 1, "side": "left"},
                "labelPrefix": "160kW直流充电桩",
            },
            {
                "id": "b",
                "stalls": 8,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "angleDeg": 0,
                "origin": {"x": 24.2, "y": 4},
                "along": "y",
                "charger": {"type": "dc_160kw", "startNo": 9, "side": "right"},
                "labelPrefix": "160kW直流充电桩",
            },
        ],
        "equipment": [
            {
                "id": "tx1",
                "type": "box_transformer",
                "x": 2.4,
                "y": 30,
                "capacityKva": 2000,
            }
        ],
        "legend": ["box_transformer", "dc_160kw", "parking"],
    }


def test_revise_1_to_8_becomes_six_trucks_not_first_drawing() -> None:
    """把 1-8 号改成 6 个重卡：不得被过期条件表修回 16 个轿车。"""
    from api.services.layouts.revise import _parse_stall_range, is_layout_revise_query
    from api.services.layouts.v2.constraints import bind_constraints_to_prior
    from api.services.layouts.parse import parse_plan

    q = "请你帮我将1-8号充电桩改成6个重卡充电桩"
    assert _parse_stall_range(q) == (1, 8)
    assert _parse_stall_range("把车位1-8改成6个重卡桩") == (1, 8)
    assert is_layout_revise_query(q, has_prior=True, has_images=False)
    prior = _dual16_payload()
    stale = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": 16, "trucks": 0, "piles": 16},
            "chargers": {"dcType": "dc_160kw"},
            "site": {"widthM": 31, "heightM": 34, "gates": [{"side": "south"}]},
        }
    )
    bound = bind_constraints_to_prior(stale, parse_plan(prior), query=q)
    assert bound.fleet.trucks == 6
    assert bound.fleet.cars == 8
    assert bound.fleet.piles == 14
    plan, _issues = prepare_v2_plan(
        prior,
        query=q,
        vision="",
        constraints=stale,
        prior=prior,
        revise=True,
    )
    trucks = [r for r in plan.parkingRows if float(r.stallLengthM) >= 10]
    cars = [r for r in plan.parkingRows if float(r.stallLengthM) < 10]
    assert sum(r.stalls for r in trucks) == 6
    assert sum(r.stalls for r in cars) == 8
    assert "truck" in (plan.legend or [])


def test_revise_1_to_8_to_320kw_keeps_other_side_160() -> None:
    """把 1-8 号改成 320kW：9-16 号保持 160kW，图纸不得整场仍是第一次的 160kW。"""
    from api.services.layouts.parse import parse_plan, plan_summary
    from api.services.layouts.revise import describe_plan_changes

    q = "请你帮我将1-8号充电桩改成320KW的直流充电桩"
    prior = _dual16_payload()
    stale = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": 16, "trucks": 0, "piles": 16},
            "chargers": {"dcType": "dc_160kw"},
            "site": {"widthM": 31, "heightM": 34, "gates": [{"side": "south"}]},
        }
    )
    plan, _issues = prepare_v2_plan(
        prior,
        query=q,
        vision="",
        constraints=stale,
        prior=prior,
        revise=True,
    )
    n320 = sum(int(r.stalls) for r in plan.parkingRows if r.charger and r.charger.type == "dc_320kw")
    n160 = sum(int(r.stalls) for r in plan.parkingRows if r.charger and r.charger.type == "dc_160kw")
    assert n320 == 8
    assert n160 == 8
    assert any("320kW" in (r.labelPrefix or "") for r in plan.parkingRows)
    assert "dc_320kw" in (plan.legend or [])
    assert "dc_160kw" in (plan.legend or [])
    text = plan_summary(plan)
    assert "320kW直流充电桩" in text
    assert "160kW直流充电桩" in text
    notes = describe_plan_changes(parse_plan(prior), plan, query=q)
    assert any("1" in n and "8" in n and "320kW" in n for n in notes)


def test_revise_27_to_50_to_160kw_survives_png_prepare() -> None:
    """27–50 号改 160kW：文案与出图前 prepare 都必须留下 24 台 160kW，不能只改说明。"""
    from api.services.layouts.pack import prepare_plan
    from api.services.layouts.parse import parse_plan, plan_summary
    from api.services.layouts.revise import describe_plan_changes

    def _row(
        rid: str, stalls: int, *, x: float, y: float, along: str, start: int, side: str
    ) -> dict:
        return {
            "id": rid,
            "stalls": stalls,
            "stallWidthM": 3,
            "stallLengthM": 6,
            "angleDeg": 0,
            "origin": {"x": x, "y": y},
            "along": along,
            "charger": {"type": "dc_320kw", "startNo": start, "side": side},
            "labelPrefix": "320kW直流充电桩",
        }

    prior = {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {
            "widthM": 100,
            "heightM": 50,
            "gate": {"side": "south", "offsetM": 91.2, "widthM": 8, "label": "出入口"},
        },
        "parkingRows": [
            _row("north", 26, x=0.8, y=43.2, along="x", start=1, side="head"),
            _row("mid", 24, x=0.8, y=22.0, along="x", start=27, side="head"),
            _row("south", 20, x=0.8, y=0.8, along="x", start=51, side="head"),
        ],
        "equipment": [],
        "legend": ["dc_320kw", "parking"],
    }
    q = "将27-50号充电桩改成160KW充电桩"
    cons = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": 70, "trucks": 0, "piles": 70},
            "chargers": {"dcType": "dc_320kw"},
            "site": {
                "widthM": 100,
                "heightM": 50,
                "shapeFrom": "user_rect",
                "gates": [{"side": "south", "along": "east"}],
            },
        }
    )
    plan, _issues = prepare_v2_plan(
        prior, query=q, vision="", constraints=cons, prior=prior, revise=True
    )

    def _count(p, kind: str) -> int:
        return sum(
            int(r.stalls) for r in p.parkingRows if r.charger and r.charger.type == kind
        )

    assert _count(plan, "dc_160kw") == 24
    assert _count(plan, "dc_320kw") == 46
    text = plan_summary(plan)
    assert "160kW直流充电桩" in text
    notes = describe_plan_changes(parse_plan(prior), plan, query=q)
    assert any("27" in n and "50" in n and "160kW" in n for n in notes)

    drawn = prepare_plan(plan, gentle=True, lock_envelope=True)
    assert _count(drawn, "dc_160kw") == 24
    assert _count(drawn, "dc_320kw") == 46
    labeled = [
        r
        for r in drawn.parkingRows
        if r.charger and r.charger.startNo == 27
    ]
    assert labeled
    assert labeled[0].charger and labeled[0].charger.type == "dc_160kw"
    assert "160kW" in (labeled[0].labelPrefix or "")


def test_bind_keeps_160kw_on_stalls_27_to_50_when_row_list_is_scrambled() -> None:
    """数组顺序是北→南→中岛时，出图重编号不得把 27–50 的 160kW 改绑到 51–70。"""
    from api.services.layouts.pack import prepare_plan
    from api.services.layouts.parse import parse_plan

    def _row(
        rid: str,
        stalls: int,
        *,
        x: float,
        y: float,
        start: int,
        kind: str,
    ) -> dict:
        kw = "160" if kind == "dc_160kw" else "320"
        return {
            "id": rid,
            "stalls": stalls,
            "stallWidthM": 3,
            "stallLengthM": 6,
            "angleDeg": 0,
            "origin": {"x": x, "y": y},
            "along": "x",
            "charger": {"type": kind, "startNo": start, "side": "head"},
            "labelPrefix": f"{kw}kW直流充电桩",
        }

    payload = {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {
            "widthM": 100,
            "heightM": 50,
            "gate": {"side": "south", "offsetM": 91.2, "widthM": 8, "label": "出入口"},
        },
        # 故意：南排在中岛前面，模拟 pack 后的数组顺序
        "parkingRows": [
            _row("north", 26, x=0.8, y=43.2, start=1, kind="dc_320kw"),
            _row("south", 20, x=0.8, y=0.8, start=51, kind="dc_320kw"),
            _row("mid", 24, x=0.8, y=22.0, start=27, kind="dc_160kw"),
        ],
        "equipment": [],
        "legend": ["dc_320kw", "dc_160kw", "parking"],
    }
    drawn = prepare_plan(parse_plan(payload), gentle=True, lock_envelope=True)

    def _type_at(no: int) -> str | None:
        for row in drawn.parkingRows:
            if not row.charger:
                continue
            start = int(row.charger.startNo)
            end = start + int(row.stalls) - 1
            if start <= no <= end:
                return str(row.charger.type)
        return None

    assert _type_at(1) == "dc_320kw"
    assert _type_at(27) == "dc_160kw"
    assert _type_at(50) == "dc_160kw"
    assert _type_at(51) == "dc_320kw"
    assert _type_at(70) == "dc_320kw"
    mid = next(r for r in drawn.parkingRows if r.charger and r.charger.startNo == 27)
    assert "160kW" in (mid.labelPrefix or "")


def test_prepare_plan_paints_27_to_50_160kw_after_numbering() -> None:
    """编号定稿后再按用户话涂色：27–50 必须变成 160kW，不能整场仍是 320kW。"""
    from api.services.layouts.pack import prepare_plan
    from api.services.layouts.parse import parse_plan
    from api.services.layouts.revise import parse_charger_type_hint, _parse_stall_range

    q = "现在我想将27-50号充电桩改成160KW的充电桩"
    assert parse_charger_type_hint(q) == "dc_160kw"
    assert _parse_stall_range(q) == (27, 50)

    def _row(rid: str, stalls: int, *, y: float, start: int) -> dict:
        return {
            "id": rid,
            "stalls": stalls,
            "stallWidthM": 3,
            "stallLengthM": 6,
            "angleDeg": 0,
            "origin": {"x": 0.8, "y": y},
            "along": "x",
            "charger": {"type": "dc_320kw", "startNo": start, "side": "head"},
            "labelPrefix": "320kW直流充电桩",
        }

    payload = {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {
            "widthM": 100,
            "heightM": 50,
            "gate": {"side": "south", "offsetM": 91.2, "widthM": 8, "label": "出入口"},
        },
        "parkingRows": [
            _row("north", 26, y=43.2, start=1),
            _row("south", 24, y=0.8, start=27),
            _row("mid", 20, y=22.0, start=51),
        ],
        "equipment": [],
        "legend": ["dc_320kw", "parking"],
    }
    drawn = prepare_plan(parse_plan(payload), gentle=True, lock_envelope=True, query=q)

    def _type_at(no: int) -> str | None:
        for row in drawn.parkingRows:
            if not row.charger:
                continue
            start = int(row.charger.startNo)
            end = start + int(row.stalls) - 1
            if start <= no <= end:
                return str(row.charger.type)
        return None

    assert _type_at(1) == "dc_320kw"
    assert _type_at(27) == "dc_160kw"
    assert _type_at(50) == "dc_160kw"
    assert _type_at(51) == "dc_320kw"
    south = next(r for r in drawn.parkingRows if r.charger and int(r.charger.startNo) == 27)
    assert "160kW" in (south.labelPrefix or "")
    assert "dc_160kw" in (drawn.legend or [])


def test_parse_accepts_charger_type_on_wrong_json_field() -> None:
    """模型常把功率写成 chargerType 或字符串，缺省不得静默变成 320kW。"""
    from api.services.layouts.parse import parse_plan

    plan = parse_plan(
        {
            "schemaVersion": "1",
            "kind": "ev_charging_station_plan",
            "site": {"widthM": 40, "heightM": 20},
            "parkingRows": [
                {
                    "id": "south",
                    "stalls": 4,
                    "stallWidthM": 3,
                    "stallLengthM": 6,
                    "origin": {"x": 1, "y": 1},
                    "along": "x",
                    "chargerType": "dc_160kw",
                    "startNo": 27,
                    "labelPrefix": "直流充电桩",
                },
                {
                    "id": "north",
                    "stalls": 4,
                    "stallWidthM": 3,
                    "stallLengthM": 6,
                    "origin": {"x": 1, "y": 12},
                    "along": "x",
                    "charger": "160kW直流充电桩",
                    "startNo": 1,
                },
            ],
        }
    )
    assert plan.parkingRows[0].charger
    assert plan.parkingRows[0].charger.type == "dc_160kw"
    assert plan.parkingRows[0].charger.startNo == 27
    assert plan.parkingRows[1].charger
    assert plan.parkingRows[1].charger.type == "dc_160kw"


def test_v2_revise_south_27_to_50_ignores_stale_sitewide_320_dc_type() -> None:
    """条件表仍写整场 320kW 时，用户点名 27-50 必须只改南排。"""
    from api.services.layouts.pack import prepare_plan
    from api.services.layouts.v2.constraints import LayoutConstraints
    from api.services.layouts.v2.pipeline import prepare_v2_plan

    def _row(rid: str, stalls: int, *, y: float, start: int) -> dict:
        return {
            "id": rid,
            "stalls": stalls,
            "stallWidthM": 3,
            "stallLengthM": 6,
            "angleDeg": 0,
            "origin": {"x": 0.8, "y": y},
            "along": "x",
            "charger": {"type": "dc_320kw", "startNo": start, "side": "head"},
            "labelPrefix": "320kW直流充电桩",
        }

    prior = {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {
            "widthM": 100,
            "heightM": 50,
            "shapeFrom": "user_rect",
            "gate": {"side": "south", "offsetM": 91.2, "widthM": 8, "label": "出入口"},
        },
        "parkingRows": [
            _row("north", 26, y=43.2, start=1),
            _row("south", 24, y=0.8, start=27),
            _row("mid", 20, y=22.0, start=51),
        ],
        "equipment": [],
        "legend": ["dc_320kw", "parking"],
    }
    q = "现在我想将27-50号充电桩改成160KW的充电桩"
    cons = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": 70, "trucks": 0, "piles": 70},
            "chargers": {"dcType": "dc_320kw"},
            "site": {
                "widthM": 100,
                "heightM": 50,
                "shapeFrom": "user_rect",
                "gates": [{"side": "south", "along": "east"}],
            },
        }
    )
    plan, _issues = prepare_v2_plan(
        prior, query=q, vision="", constraints=cons, prior=prior, revise=True
    )
    drawn = prepare_plan(plan, gentle=True, lock_envelope=True, query=q)

    def _type_at(p, no: int) -> str | None:
        for row in p.parkingRows:
            if not row.charger:
                continue
            start = int(row.charger.startNo)
            end = start + int(row.stalls) - 1
            if start <= no <= end:
                return str(row.charger.type)
        return None

    assert _type_at(drawn, 1) == "dc_320kw"
    assert _type_at(drawn, 27) == "dc_160kw"
    assert _type_at(drawn, 50) == "dc_160kw"
    assert _type_at(drawn, 51) == "dc_320kw"
    assert "dc_160kw" in (drawn.legend or [])


def test_brief_parses_southeast_from_query_and_vision() -> None:
    from api.services.layouts.brief import parse_layout_brief

    q = parse_layout_brief("如图所示场地约1000平米，出入口在东南角，布置16台160kW直流充电桩")
    assert q.gate_side == "south"
    assert q.gate_along == "east"
    assert q.gate_east is True
    vis = parse_layout_brief(
        "如图所示布置16台160kW直流充电桩",
        "外形：矩形\n出入口：south corner=southeast along=east\npolygon: []",
    )
    assert vis.gate_side == "south"
    assert vis.gate_along == "east"


def test_first_draw_puts_southeast_gate_not_south_center() -> None:
    """草稿/用户写东南角时，不得把门画在南墙正中。"""
    from api.services.layouts.schema import collect_site_gates

    centered = _dual16_payload()
    cons = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": 16, "trucks": 0, "piles": 16},
            "site": {
                "widthM": 31,
                "heightM": 34,
                "gates": [{"side": "south"}],
            },
        }
    )
    plan, _issues = prepare_v2_plan(
        centered,
        query="如图所示这个场地大概1000平米，布置16台160KW直流充电桩",
        vision="外形：矩形。出入口写在东南角，corner=southeast，along=east。",
        constraints=cons,
    )
    gates = collect_site_gates(plan.site)
    assert gates
    gate = gates[0]
    assert gate.side == "south"
    mid = float(gate.offsetM) + float(gate.widthM) / 2.0
    assert mid > float(plan.site.widthM) * 0.58, (
        f"东南角门应在南墙偏东，当前中心 x={mid:g} / 宽={plan.site.widthM:g}"
    )
    center = float(plan.site.widthM) / 2.0
    assert abs(mid - center) > 2.0


def test_dual_wall_keeps_southeast_gate_and_clears_drive() -> None:
    """草稿大门在东南角时，>4 车双侧靠墙只重排车位，不得把门改到南墙正中。"""
    from api.services.layouts.pack import (
        _aisle_aabb,
        _infer_row_wall,
        _overlap,
        _row_aabb,
    )
    from api.services.layouts.schema import collect_site_gates

    se_off = 21.0
    payload = _dual16_payload()
    payload["site"]["gate"] = {
        "side": "south",
        "offsetM": se_off,
        "widthM": 8,
        "label": "出入口",
    }
    cons = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": 16, "trucks": 0, "piles": 16},
            "site": {
                "widthM": 31,
                "heightM": 34,
                "gates": [{"side": "south", "along": "east", "widthM": 8}],
            },
        }
    )
    plan, _issues = prepare_v2_plan(
        payload,
        query="如图所示场地布置16台160kW充电桩，大于4辆时两侧靠墙",
        vision="外形：矩形。出入口写在东南角，corner=southeast，along=east。",
        constraints=cons,
    )
    gates = collect_site_gates(plan.site)
    assert gates and gates[0].side == "south"
    mid = float(gates[0].offsetM) + float(gates[0].widthM) / 2.0
    assert mid > float(plan.site.widthM) * 0.58, (
        f"东南门被改走了：中心 x={mid:g} / 宽={plan.site.widthM:g}"
    )
    walls = {_infer_row_wall(plan, row) for row in plan.parkingRows} - {None}
    assert "east" not in walls, "东南门贴东墙时，小场地东墙应留给进场车道"
    assert "south" not in walls, "南墙是大门边，小场地不应再贴车"
    assert "west" in walls
    assert sum(int(r.stalls) for r in plan.parkingRows) >= 12
    throat = (
        float(gates[0].offsetM) - 0.5,
        0.0,
        float(gates[0].offsetM) + float(gates[0].widthM) + 0.5,
        5.5,
    )
    stall_boxes = [_row_aabb(r) for r in plan.parkingRows]
    for sb in stall_boxes:
        assert not _overlap(sb, throat, pad=-0.15), "充电车位不得压进东南门洞"
    drive = next(a for a in plan.aisles if a.id == "aisle_drive")
    dbox = _aisle_aabb(drive)
    assert dbox is not None
    for sb in stall_boxes:
        assert not _overlap(dbox, sb, pad=-0.2), "行驶路线不得与充电车位重合"
    d0, d1 = drive.centerline[0], drive.centerline[-1]
    vertical = abs(d0.x - d1.x) <= abs(d0.y - d1.y) + 0.05
    assert vertical, "南北向大门应对准一条竖向过道"
    cx = 0.5 * (float(d0.x) + float(d1.x))
    assert abs(cx - mid) <= 2.2, f"过道应对准大门：过道x={cx:g} 门={mid:g}"
    connector = next((a for a in plan.aisles if a.id == "aisle_gate"), None)
    if abs(cx - mid) <= 1.6:
        assert connector is None


def test_compact_yard_circulation_first_gate_aligned_and_hosts_off_road() -> None:
    """约 32×32 小场地：先留正对大门的路，车位不贴进场墙，群冲不得压过道。"""
    from api.services.layouts.brief import is_electrical_room
    from api.services.layouts.pack import (
        _aisle_aabb,
        _eq_box,
        _gate_spine_keepouts,
        _infer_row_wall,
        _overlap,
        _row_aabb,
    )
    from api.services.layouts.rules import CAR_AISLE_M
    from api.services.layouts.schema import collect_site_gates

    query = (
        "一块约1000平米的正方形场地，东西32米南北32米，北侧偏东一个出入口。"
        "布置12台160kW直流充电桩，增加一个配电室，配电室里有两台箱变，"
        "还要两台群冲主机柜。"
    )
    cons = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": 12, "trucks": 0, "piles": 12},
            "chargers": {"dcType": "dc_160kw"},
            "site": {
                "widthM": 32,
                "heightM": 32,
                "areaM2": 1024,
                "gates": [{"side": "north", "along": "east", "widthM": 8}],
            },
        }
    )
    plan, issues = prepare_v2_plan(
        {"kind": "ev_charging_station_constraints"},
        query=query,
        vision="外形：矩形。出入口在北墙偏东，corner=northeast，along=east。",
        constraints=cons,
    )
    assert abs(float(plan.site.widthM) * float(plan.site.heightM) - 1024) / 1024 < 0.25
    assert sum(int(r.stalls) for r in plan.parkingRows) == 12
    gates = collect_site_gates(plan.site)
    assert gates and gates[0].side == "north"
    mid = float(gates[0].offsetM) + float(gates[0].widthM) / 2.0
    assert mid > float(plan.site.widthM) * 0.55
    walls = {_infer_row_wall(plan, r) for r in plan.parkingRows} - {None}
    assert "east" not in walls, "东北门贴东墙时东侧不应再贴一列车把路挤歪"
    assert "north" not in walls, "北墙是大门边，不应贴车"
    drive = next(a for a in plan.aisles if a.id == "aisle_drive")
    d0, d1 = drive.centerline[0], drive.centerline[-1]
    assert abs(d0.x - d1.x) <= abs(d0.y - d1.y) + 0.05
    cx = 0.5 * (float(d0.x) + float(d1.x))
    assert abs(cx - mid) <= 2.2, f"马路应正对大门：过道x={cx:g} 门={mid:g}"
    stall_boxes = [_row_aabb(r) for r in plan.parkingRows]
    for spine in _gate_spine_keepouts(plan):
        for sb in stall_boxes:
            assert not _overlap(sb, spine, pad=-0.2), "车位不得占用正对大门的行车走廊"
    dbox = _aisle_aabb(drive)
    assert dbox is not None
    for sb in stall_boxes:
        assert not _overlap(dbox, sb, pad=-0.2)
    hosts = [e for e in plan.equipment if e.type == "group_host"]
    assert len(hosts) == 2
    rooms = [b for b in plan.buildings if is_electrical_room(b)]
    assert rooms
    aisle_boxes = [box for a in plan.aisles if (box := _aisle_aabb(a)) is not None]
    for eq in plan.equipment:
        if eq.type in {"box_transformer", "group_host"}:
            eb = _eq_box(float(eq.x), float(eq.y))
            for ab in aisle_boxes:
                assert not _overlap(eb, ab, pad=-0.15), f"{eq.type} 不得压在过道上"
            for spine in _gate_spine_keepouts(plan):
                assert not _overlap(eb, spine, pad=-0.15), f"{eq.type} 不得占用行车走廊"
    blocking = [i for i in issues if i.blocking]
    assert not any(i.code == "aisle_clear" for i in blocking)
    assert float(drive.widthM) + 1e-6 >= CAR_AISLE_M * 0.85


def test_100x50_70_piles_keeps_envelope_and_se_gate() -> None:
    """东西 100×南北 50、70 桩、东南门贴东墙：不得拉成细长场地，门不得改到南中或东墙。"""
    from api.services.layouts.brief import parse_layout_brief
    from api.services.layouts.schema import collect_site_gates

    query = (
        "我有一块场地大概是5000平米的长方形，东西长100米，南北宽50米，"
        "大门在东南角，紧挨着东墙。计划安装70台可充电的320KW直流充电桩。"
    )
    brief = parse_layout_brief(query)
    assert brief.site_w == 100
    assert brief.site_h == 50
    assert brief.cars == 70
    assert brief.gate_side == "south"
    assert brief.gate_along == "east"

    cons = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": 70, "trucks": 0, "piles": 70},
            "chargers": {"dcType": "dc_320kw"},
            "site": {
                "widthM": 100,
                "heightM": 50,
                "areaM2": 5000,
                "gates": [{"side": "east"}],
            },
        }
    )
    plan, issues = prepare_v2_plan(
        {"kind": "ev_charging_station_constraints"},
        query=query,
        vision="",
        constraints=cons,
    )
    assert plan.site.widthM == pytest.approx(100, abs=0.6)
    assert plan.site.heightM == pytest.approx(50, abs=0.6)
    area = float(plan.site.widthM) * float(plan.site.heightM)
    assert abs(area - 5000) / 5000 < 0.2
    assert sum(int(r.stalls) for r in plan.parkingRows) == 70
    gates = collect_site_gates(plan.site)
    assert gates and gates[0].side == "south"
    mid = float(gates[0].offsetM) + float(gates[0].widthM) / 2.0
    assert mid > plan.site.widthM * 0.7, f"门应紧贴东墙，中心 x={mid:g}"
    blocking = [i for i in issues if i.blocking]
    assert not any(i.code in {"site_area", "gates", "pile_count", "stall_total"} for i in blocking)
    from api.services.layouts.pack import (
        _aisle_aabb,
        _eq_box,
        _infer_row_wall,
        _l_corners_blocked,
        _overlap,
        _row_aabb,
    )
    from api.services.layouts.rules import CAR_AISLE_M

    aisle_notes = [i for i in issues if i.code == "aisle_width"]
    assert not aisle_notes, [i.message for i in aisle_notes]
    assert not _l_corners_blocked(plan), "转角车位必须留出回转，不能四墙对撞"
    assert not any(i.code == "corner_egress" for i in issues)
    gate_aisle = next((a for a in plan.aisles if a.id == "aisle_gate"), None)
    if gate_aisle is not None:
        assert float(gate_aisle.widthM) + 1e-6 >= CAR_AISLE_M * 0.85
    inner = [
        r
        for r in plan.parkingRows
        if str(r.id or "").startswith(("cars_island", "cars_wrap"))
    ]
    wall_rows = [r for r in plan.parkingRows if r not in inner]
    throat = (
        float(gates[0].offsetM) - 0.5,
        0.0,
        float(gates[0].offsetM) + float(gates[0].widthM) + 0.5,
        CAR_AISLE_M,
    )
    walls = {_infer_row_wall(plan, r) for r in wall_rows}
    assert not ({"north", "west"} <= walls and {"north", "east"} <= walls) or not _l_corners_blocked(plan)
    for row in plan.parkingRows:
        aid = f"aisle_{row.id}"
        assert any(a.id == aid or a.id in {"aisle_drive", "aisle_gate"} for a in plan.aisles)
    for island in inner:
        assert _infer_row_wall(plan, island) is None
        ib = _row_aabb(island)
        assert not _overlap(ib, throat, pad=-0.15)
        for wr in wall_rows:
            assert not _overlap(ib, _row_aabb(wr), pad=CAR_AISLE_M * 0.8)
    aisle_boxes = [box for a in plan.aisles if (box := _aisle_aabb(a)) is not None]
    for eq in plan.equipment:
        eb = _eq_box(float(eq.x), float(eq.y))
        for ab in aisle_boxes:
            assert not _overlap(eb, ab, pad=-0.15), f"{eq.type} 不得压在过道上"


def test_100x50_70_piles_repacks_drive_leftover_into_island() -> None:
    """模型已输出四墙+过道余量时，锁场地仍按贴墙容量重排，余量居中，车道≥7m。"""
    from api.services.layouts.pack import _infer_row_wall, _l_corners_blocked, _overlap, _row_aabb
    from api.services.layouts.rules import CAR_AISLE_M

    def _row(
        rid: str,
        stalls: int,
        *,
        x: float,
        y: float,
        along: str,
        start: int,
        side: str,
    ) -> dict:
        return {
            "id": rid,
            "stalls": stalls,
            "stallWidthM": 3,
            "stallLengthM": 6,
            "angleDeg": 0,
            "origin": {"x": x, "y": y},
            "along": along,
            "charger": {"type": "dc_320kw", "startNo": start, "side": side},
            "labelPrefix": "320kW直流充电桩",
        }

    query = (
        "我有一块场地大概是5000平米的长方形，东西长100米，南北宽50米，"
        "大门在东南角，紧挨着东墙。计划安装70台可充电的320KW直流充电桩。"
    )
    payload = {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {
            "widthM": 100,
            "heightM": 50,
            "gate": {"side": "south", "offsetM": 91.2, "widthM": 8, "label": "出入口"},
        },
        "parkingRows": [
            _row("cars_north", 26, x=0.4, y=43.2, along="x", start=1, side="head"),
            _row("cars_south", 24, x=0.4, y=0.8, along="x", start=27, side="head"),
            _row("cars_west", 9, x=0.8, y=9.0, along="y", start=51, side="left"),
            _row("cars_east", 8, x=93.2, y=8.0, along="y", start=60, side="right"),
            _row("cars_mid", 3, x=46.0, y=22.0, along="x", start=68, side="head"),
        ],
        "equipment": [{"id": "tx1", "type": "box_transformer", "x": 96, "y": 46, "capacityKva": 2000}],
        "legend": ["box_transformer", "dc_320kw", "parking"],
    }
    cons = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": 70, "trucks": 0, "piles": 70},
            "chargers": {"dcType": "dc_320kw"},
            "site": {
                "widthM": 100,
                "heightM": 50,
                "areaM2": 5000,
                "shapeFrom": "user_rect",
                "gates": [{"side": "south", "along": "east"}],
            },
        }
    )
    plan, issues = prepare_v2_plan(payload, query=query, vision="", constraints=cons)
    assert plan.site.widthM == pytest.approx(100, abs=0.6)
    assert plan.site.heightM == pytest.approx(50, abs=0.6)
    assert sum(int(r.stalls) for r in plan.parkingRows) == 70
    assert not [
        i for i in issues if i.code in {"aisle_width", "corner_egress", "aisle_clear"}
    ], [i.message for i in issues]
    inner = [
        r
        for r in plan.parkingRows
        if str(r.id or "").startswith(("cars_island", "cars_wrap"))
    ]
    assert inner, "贴墙转角倒不出时余量应换行，而不是留在过道"
    for island in inner:
        assert _infer_row_wall(plan, island) is None
        ib = _row_aabb(island)
        for wr in plan.parkingRows:
            if wr in inner:
                continue
            assert not _overlap(ib, _row_aabb(wr), pad=CAR_AISLE_M * 0.8)
    assert not _l_corners_blocked(plan)
    gate_aisle = next((a for a in plan.aisles if a.id == "aisle_gate"), None)
    if gate_aisle is not None:
        assert float(gate_aisle.widthM) + 1e-6 >= CAR_AISLE_M * 0.85


def test_parse_electrical_room_and_group_hosts() -> None:
    from api.services.layouts.brief import parse_layout_brief

    q = (
        "请结合刚才绘制的图增加一个配电室，配电室里有两台1000KVA箱变，"
        "还要增加两台960KW群冲主机柜，不要影响现在的布局"
    )
    brief = parse_layout_brief(q)
    assert brief.electrical_room is True
    assert brief.transformer_n == 2
    assert brief.transformer_kva == 1000
    assert brief.host_n == 2
    assert brief.host_kw == 960
    assert brief.cars is None


def test_first_draw_without_transformer_request_drops_seeded_feeder() -> None:
    """没点名箱变时，不因模型示例/seed 强行画一台，更不能落到车道上。"""
    from api.services.layouts.pack import _aisle_aabb, _eq_box, _overlap

    query = (
        "我有一块场地大概是5000平米的长方形，东西长100米，南北宽50米，"
        "大门在东南角，紧挨着东墙。计划安装70台可充电的320KW直流充电桩。"
    )
    cons = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": 70, "trucks": 0, "piles": 70},
            "chargers": {"dcType": "dc_320kw"},
            "site": {
                "widthM": 100,
                "heightM": 50,
                "areaM2": 5000,
                "shapeFrom": "user_rect",
                "gates": [{"side": "south", "along": "east", "widthM": 8}],
            },
            "transformers": [],
        }
    )
    payload = {
        "kind": "ev_charging_station_plan",
        "site": {"widthM": 100, "heightM": 50, "gate": {"side": "south", "offsetM": 91, "widthM": 8}},
        "parkingRows": [],
        "equipment": [
            {
                "id": "tx1",
                "type": "box_transformer",
                "x": 50,
                "y": 28,
                "label": "箱变",
                "capacityKva": 2000,
            }
        ],
    }
    plan, _issues = prepare_v2_plan(
        payload, query=query, vision="", constraints=cons
    )
    txs = [e for e in plan.equipment if e.type == "box_transformer"]
    assert txs == [], "用户没要求箱变时不应出图"
    assert "box_transformer" not in (plan.legend or [])
    aisle_boxes = [box for a in plan.aisles if (box := _aisle_aabb(a)) is not None]
    for eq in plan.equipment:
        eb = _eq_box(float(eq.x), float(eq.y))
        for ab in aisle_boxes:
            assert not _overlap(eb, ab, pad=-0.15)


def test_revise_adds_electrical_room_and_group_hosts_on_blank() -> None:
    """第二次只加配电室/箱变/群冲：车位不动，箱变进室内，主机柜在空白地且不压过道。"""
    from api.services.layouts.brief import is_electrical_room, parse_layout_brief
    from api.services.layouts.pack import _aisle_aabb, _eq_box, _overlap, _row_aabb

    query1 = (
        "我有一块场地大概是5000平米的长方形，东西长100米，南北宽50米，"
        "大门在东南角，紧挨着东墙。计划安装70台可充电的320KW直流充电桩。"
    )
    cons = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": 70, "trucks": 0, "piles": 70},
            "chargers": {"dcType": "dc_320kw"},
            "site": {
                "widthM": 100,
                "heightM": 50,
                "areaM2": 5000,
                "shapeFrom": "user_rect",
                "gates": [{"side": "south", "along": "east", "widthM": 8}],
            },
        }
    )
    prior, _issues = prepare_v2_plan(
        {"kind": "ev_charging_station_constraints"},
        query=query1,
        vision="",
        constraints=cons,
    )
    stall_sig = [(r.id, r.origin.x, r.origin.y, r.stalls, r.along) for r in prior.parkingRows]
    query2 = (
        "请结合刚才绘制的图增加一个配电室，配电室里有两台1000KVA箱变，"
        "还要增加两台960KW群冲主机柜，不要影响现在的布局"
    )
    brief = parse_layout_brief(query2)
    assert brief.electrical_room and brief.host_n == 2 and brief.transformer_kva == 1000
    plan, issues = prepare_v2_plan(
        {"kind": "ev_charging_station_constraints"},
        query=query2,
        vision="",
        constraints=cons,
        prior=prior,
        revise=True,
    )
    assert [(r.id, r.origin.x, r.origin.y, r.stalls, r.along) for r in plan.parkingRows] == stall_sig
    rooms = [b for b in plan.buildings if is_electrical_room(b)]
    assert rooms, "应画出配电室"
    room = rooms[0].rect
    rb = (float(room.x), float(room.y), float(room.x) + float(room.w), float(room.y) + float(room.h))
    txs = [e for e in plan.equipment if e.type == "box_transformer"]
    assert len(txs) == 2
    assert all(e.capacityKva == 1000 for e in txs)
    assert all("1000" in (e.label or "") for e in txs)
    for tx in txs:
        assert rb[0] < tx.x < rb[2] and rb[1] < tx.y < rb[3], "箱变必须在配电室内"
    hosts = [e for e in plan.equipment if e.type == "group_host"]
    assert len(hosts) == 2, "应画出两台群冲主机柜"
    assert all("960" in (e.label or "") for e in hosts)
    stall_boxes = [_row_aabb(r) for r in plan.parkingRows]
    aisle_boxes = [box for a in plan.aisles if (box := _aisle_aabb(a)) is not None]
    for eq in txs + hosts:
        eb = _eq_box(float(eq.x), float(eq.y))
        for sb in stall_boxes:
            assert not _overlap(eb, sb, pad=-0.15), f"{eq.type} 不得压车位"
        for ab in aisle_boxes:
            assert not _overlap(eb, ab, pad=-0.15), f"{eq.label or eq.type} 不得压过道"
    for ab in aisle_boxes:
        assert not _overlap(rb, ab, pad=-0.1), "配电室不得压过道"
    assert not any(i.code == "aisle_clear" and i.blocking for i in issues)


def test_null_constraints_follow_5000m2_50_piles() -> None:
    q = "场地约5000㎡，布置50台160kW充电桩，两台箱变"
    empty = LayoutConstraints.model_validate(
        {
            "fleet": {"cars": None, "trucks": None, "piles": None},
            "chargers": {"dcType": None},
            "transformers": [],
            "site": {"areaM2": None, "widthM": None, "heightM": None, "gates": []},
        }
    )
    filled = overlay_user_text_on_constraints(empty, q, overwrite_counts=True)
    assert filled.fleet.cars == 50
    assert filled.chargers.dcType == "dc_160kw"
    assert filled.transformers[0].count == 2
    assert filled.site.areaM2 == 5000

    seeded = seed_plan_payload(empty, query=q)
    assert sum(int(r["stalls"]) for r in seeded["parkingRows"]) == 50
    assert "dc_160kw" in seeded["legend"]
    assert sum(1 for e in seeded["equipment"] if e["type"] == "box_transformer") == 2

    copied = LayoutConstraints.model_validate(
        {"fleet": {"cars": 8, "trucks": 0, "piles": 8}}
    )
    plan, _issues = prepare_v2_plan(
        {"kind": "ev_charging_station_plan"},
        query=q,
        vision="",
        constraints=copied,
        prior=None,
        revise=False,
    )
    assert sum(int(r.stalls) for r in plan.parkingRows) == 50
    assert sum(1 for e in plan.equipment if e.type == "box_transformer") == 2
    area = float(plan.site.widthM) * float(plan.site.heightM)
    assert abs(area - 5000) / 5000 < 0.25
    aisle_ids = {str(a.id) for a in plan.aisles}
    assert "aisle_drive" in aisle_ids
    assert "aisle_turn" in aisle_ids or any("进场" in (a.label or "") for a in plan.aisles)


def test_seed_without_count_does_not_invent_eight() -> None:
    cons = LayoutConstraints.model_validate({"fleet": {}})
    seeded = seed_plan_payload(cons, query="")
    assert sum(int(r["stalls"]) for r in seeded["parkingRows"]) == 0


def test_prepare_traces_draft_parking_origins() -> None:
    from api.services.layouts.draft import parse_parking_rows_from_text

    vision = """外形：矩形
polygon: [{"x":0,"y":0},{"x":40,"y":0},{"x":40,"y":30},{"x":0,"y":30}]
parkingRows: [{"id":"cars","stalls":6,"stallWidthM":3,"stallLengthM":6,"angleDeg":0,"origin":{"x":2.4,"y":8.1},"along":"y","charger":{"type":"none","side":"left"}}]
"""
    rows = parse_parking_rows_from_text(vision)
    assert len(rows) == 1
    assert rows[0].stalls == 6
    cons = LayoutConstraints.model_validate({"fleet": {}, "site": {"widthM": 40, "heightM": 30}})
    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["parkingRows"] = [
        {
            "id": "moved",
            "stalls": 8,
            "stallWidthM": 3,
            "stallLengthM": 6,
            "angleDeg": 0,
            "origin": {"x": 30, "y": 1},
            "along": "x",
            "charger": {"type": "dc_160kw", "startNo": 1, "side": "head"},
        }
    ]
    plan, _ = prepare_v2_plan(payload, query="按草稿出布置图", vision=vision, constraints=cons)
    assert sum(int(r.stalls) for r in plan.parkingRows) == 6
    assert abs(float(plan.parkingRows[0].origin.x) - 2.4) < 0.35
    assert abs(float(plan.parkingRows[0].origin.y) - 8.1) < 0.35
    assert plan.parkingRows[0].along == "y"

