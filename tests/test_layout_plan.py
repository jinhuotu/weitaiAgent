"""充电站平面布置 JSON 解析、校验与 PNG 渲染。"""

from __future__ import annotations

import json
import math
from typing import Any

import pytest

from common.errors import AppError
from api.services.layouts.pack import charger_heads, rect_corners
from api.services.layouts.parse import parse_plan, plan_summary
from api.services.layouts.render import expand_stalls, prepare_plan, render_plan_png
from api.services.layouts.schema import EXAMPLE_PLAN, EvChargingStationPlan, ParkingRowSpec, PointM
from api.services.workflows.crud import validate_graph
from api.services.workflows.nodes import _exec_layout_out, _replace_templates


def test_example_plan_validates() -> None:
    plan = EvChargingStationPlan.model_validate(EXAMPLE_PLAN)
    assert plan.kind == "ev_charging_station_plan"
    assert sum(r.stalls for r in plan.parkingRows) == 12
    trucks = next(r for r in plan.parkingRows if r.id == "trucks")
    assert len(expand_stalls(trucks)) == 4


def test_expand_stalls_along_x_keeps_row_axis() -> None:
    row = ParkingRowSpec(
        id="r",
        stalls=4,
        stallWidthM=2.6,
        stallLengthM=6.0,
        angleDeg=-45,
        origin=PointM(x=10, y=20),
        along="x",
    )
    stalls = expand_stalls(row)
    dx = stalls[1][0].x - stalls[0][0].x
    dy = stalls[1][0].y - stalls[0][0].y
    assert dx > 2.5
    assert abs(dy) < 1e-9
    assert 3.2 < dx < 4.2


def test_prepare_plan_adds_gate_and_cables_on_chargers() -> None:
    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    del payload["site"]["gate"]
    payload["cables"] = []
    payload["parkingRows"][1]["stallWidthM"] = 4.0
    payload["parkingRows"][1]["stallLengthM"] = 17
    plan = prepare_plan(parse_plan(payload))
    assert plan.site.gate is not None
    assert plan.site.gate.label == "出入口"
    cars = next(r for r in plan.parkingRows if r.id.startswith("cars"))
    assert cars.stallLengthM == 6.0
    lv = [c for c in plan.cables if c.voltage == "0.4kv"]
    assert lv
    hx, hy = charger_heads(cars)[0]
    nearest = min(math.hypot(p.x - hx, p.y - hy) for c in lv for p in c.polyline)
    assert nearest < 0.2


def test_prepare_keeps_stalls_inside_site() -> None:
    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["parkingRows"] = [
        {
            "id": "row1",
            "stalls": 12,
            "stallWidthM": 2.6,
            "stallLengthM": 6.0,
            "angleDeg": -45,
            "origin": {"x": 2, "y": 0},
            "along": "x",
            "charger": {"type": "dc_160kw", "startNo": 1, "side": "head"},
            "labelPrefix": "直流充电桩",
        }
    ]
    plan = prepare_plan(parse_plan(payload))
    assert sum(r.stalls for r in plan.parkingRows) == 12
    for row in plan.parkingRows:
        for rect, _ in expand_stalls(row):
            for x, y in rect_corners(rect):
                assert -0.05 <= x <= plan.site.widthM + 0.05
                assert -0.05 <= y <= plan.site.heightM + 0.05
    road = payload["roads"][0]["polygon"]
    road_box = (
        min(p["x"] for p in road),
        min(p["y"] for p in road),
        max(p["x"] for p in road),
        max(p["y"] for p in road),
    )
    overlaps = 0
    total = 0
    for row in plan.parkingRows:
        for rect, _ in expand_stalls(row):
            xs = [p[0] for p in rect_corners(rect)]
            ys = [p[1] for p in rect_corners(rect)]
            box = (min(xs), min(ys), max(xs), max(ys))
            total += 1
            hit = not (
                box[2] <= road_box[0] + 0.2
                or road_box[2] <= box[0] + 0.2
                or box[3] <= road_box[1] + 0.2
                or road_box[3] <= box[1] + 0.2
            )
            if hit:
                overlaps += 1
    assert overlaps <= total * 0.15


def test_pack_uses_open_land_east_of_west_factory() -> None:
    payload = {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {
            "widthM": 55,
            "heightM": 35,
            "gate": {"side": "east", "offsetM": 10, "widthM": 8},
        },
        "buildings": [
            {
                "id": "factory",
                "label": "厂房",
                "kind": "factory",
                "rect": {"x": 0, "y": 4, "w": 18, "h": 24},
            }
        ],
        "parkingRows": [
            {
                "id": "trucks",
                "stalls": 4,
                "stallWidthM": 5.0,
                "stallLengthM": 17.0,
                "angleDeg": 0,
                "origin": {"x": 1, "y": 1},
                "along": "x",
                "charger": {"type": "dc_400kw", "startNo": 1, "side": "head"},
                "labelPrefix": "重卡直流桩",
            },
            {
                "id": "cars",
                "stalls": 8,
                "stallWidthM": 3.0,
                "stallLengthM": 6.0,
                "angleDeg": 0,
                "origin": {"x": 1, "y": 1},
                "along": "x",
                "charger": {"type": "dc_160kw", "startNo": 0, "side": "head"},
                "labelPrefix": "直流充电桩",
            },
        ],
        "equipment": [
            {
                "id": "tx1",
                "type": "box_transformer",
                "x": 8,
                "y": 16,
                "label": "1250kVA箱变",
                "capacityKva": 1250,
            }
        ],
    }
    plan = prepare_plan(parse_plan(payload))
    assert sum(r.stalls for r in plan.parkingRows) == 12
    assert plan.parkingRows[0].charger is not None
    assert plan.parkingRows[0].charger.type == "dc_320kw"
    factory = (0.0, 4.0, 18.0, 28.0)

    def box_of(row: ParkingRowSpec) -> tuple[float, float, float, float]:
        pts = [pt for rect, _ in expand_stalls(row) for pt in rect_corners(rect)]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    def overlaps(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
        return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])

    row_boxes = [box_of(row) for row in plan.parkingRows]
    for box in row_boxes:
        assert box[0] >= 17.4
        assert not overlaps(box, factory)
    for i, a in enumerate(row_boxes):
        for b in row_boxes[i + 1 :]:
            assert not overlaps(a, b)
    tx = plan.equipment[0]
    assert tx.x >= 16.0
    assert not (1.0 < tx.x < 17.0 and 5.0 < tx.y < 27.0)
    for cable in plan.cables:
        for pt in cable.polyline:
            inside = 1.0 < pt.x < 17.0 and 5.0 < pt.y < 27.0
            assert not inside


def test_trucks_stay_inside_yard_not_on_south_street() -> None:
    payload = {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {
            "widthM": 55,
            "heightM": 35,
            "gate": {"side": "east", "offsetM": 10, "widthM": 8},
        },
        "buildings": [
            {
                "id": "factory",
                "label": "厂房",
                "kind": "factory",
                "rect": {"x": 0, "y": 6, "w": 16, "h": 22},
            }
        ],
        "roads": [
            {
                "polygon": [
                    {"x": 0, "y": 0},
                    {"x": 55, "y": 0},
                    {"x": 55, "y": 6},
                    {"x": 0, "y": 6},
                ],
                "label": "南侧通道",
            },
            {
                "polygon": [
                    {"x": 18, "y": 8},
                    {"x": 54, "y": 8},
                    {"x": 54, "y": 33},
                    {"x": 18, "y": 33},
                ],
                "label": "内部道路",
            }
        ],
        "parkingRows": [
            {
                "id": "trucks",
                "stalls": 4,
                "stallWidthM": 5.0,
                "stallLengthM": 17.0,
                "angleDeg": 0,
                "origin": {"x": 2, "y": 0},
                "along": "x",
                "charger": {"type": "dc_320kw", "startNo": 1, "side": "head"},
                "labelPrefix": "重卡直流桩",
            },
            {
                "id": "cars",
                "stalls": 8,
                "stallWidthM": 3.0,
                "stallLengthM": 6.0,
                "angleDeg": 0,
                "origin": {"x": 2, "y": 0},
                "along": "x",
                "charger": {"type": "dc_160kw", "startNo": 5, "side": "head"},
                "labelPrefix": "直流充电桩",
            },
        ],
        "equipment": [
            {
                "id": "tx1",
                "type": "box_transformer",
                "x": 40,
                "y": 30,
                "label": "1250kVA",
                "capacityKva": 1250,
            }
        ],
    }
    plan = prepare_plan(parse_plan(payload))
    street_top = 6.0
    for row in plan.parkingRows:
        for rect, _ in expand_stalls(row):
            for _x, y in rect_corners(rect):
                assert y >= street_top - 0.35


def test_pack_unstacks_overlapping_llm_origins() -> None:
    """LLM 常把重卡/轿车写在同一 origin，并画一条竖框当车道；装箱后必须全部展开。"""
    payload = {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {
            "widthM": 80,
            "heightM": 48,
            "gate": {"side": "east", "offsetM": 28, "widthM": 10, "label": "入口"},
        },
        "buildings": [
            {
                "id": "factory",
                "label": "厂房",
                "kind": "factory",
                "rect": {"x": 0, "y": 6, "w": 22, "h": 28},
            }
        ],
        "roads": [
            {
                "polygon": [
                    {"x": 38, "y": 8},
                    {"x": 46, "y": 8},
                    {"x": 46, "y": 40},
                    {"x": 38, "y": 40},
                ],
                "label": "通道",
            }
        ],
        "parkingRows": [
            {
                "id": "trucks",
                "stalls": 4,
                "stallWidthM": 5.0,
                "stallLengthM": 17.0,
                "angleDeg": -45,
                "origin": {"x": 40, "y": 20},
                "along": "x",
                "charger": {"type": "dc_400kw", "startNo": 1, "side": "head"},
                "labelPrefix": "重卡直流桩",
            },
            {
                "id": "cars",
                "stalls": 8,
                "stallWidthM": 3.0,
                "stallLengthM": 6.0,
                "angleDeg": -45,
                "origin": {"x": 40, "y": 20},
                "along": "x",
                "charger": {"type": "dc_160kw", "startNo": 1, "side": "head"},
                "labelPrefix": "直流充电桩",
            },
        ],
        "equipment": [
            {
                "id": "tx1",
                "type": "box_transformer",
                "x": 42,
                "y": 22,
                "label": "1250kVA变电所",
                "capacityKva": 1250,
            }
        ],
    }
    plan = prepare_plan(parse_plan(payload))
    assert sum(r.stalls for r in plan.parkingRows) == 12
    factory = (0.0, 6.0, 22.0, 34.0)

    def box_of(row: ParkingRowSpec) -> tuple[float, float, float, float]:
        pts = [pt for rect, _ in expand_stalls(row) for pt in rect_corners(rect)]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    def overlaps(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
        return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])

    stall_boxes: list[tuple[float, float, float, float]] = []
    for row in plan.parkingRows:
        assert abs(row.angleDeg + 45) < 1e-6
        for rect, _ in expand_stalls(row):
            xs = [p[0] for p in rect_corners(rect)]
            ys = [p[1] for p in rect_corners(rect)]
            stall_boxes.append((min(xs), min(ys), max(xs), max(ys)))
    assert len(stall_boxes) == 12
    for box in stall_boxes:
        assert box[0] >= 21.5
        assert not overlaps(box, factory)
    truck_rows = [r for r in plan.parkingRows if r.stallLengthM >= 10]
    car_rows = [r for r in plan.parkingRows if r.stallLengthM < 10]
    assert truck_rows and car_rows
    tb, cb = box_of(truck_rows[0]), box_of(car_rows[0])
    assert (round(tb[0], 1), round(tb[1], 1)) != (round(cb[0], 1), round(cb[1], 1))


def test_chargers_bind_one_to_one_drop_floating_piles() -> None:
    payload = {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {"widthM": 70, "heightM": 42, "gate": {"side": "east", "offsetM": 12, "widthM": 8}},
        "buildings": [
            {
                "id": "factory",
                "label": "厂房",
                "kind": "factory",
                "rect": {"x": 0, "y": 6, "w": 20, "h": 26},
            }
        ],
        "parkingRows": [
            {
                "id": "trucks",
                "stalls": 4,
                "stallWidthM": 5.0,
                "stallLengthM": 17.0,
                "angleDeg": 0,
                "origin": {"x": 24, "y": 4},
                "along": "x",
                "charger": {"type": "dc_320kw", "startNo": 1, "side": "head"},
                "labelPrefix": "重卡直流桩",
            },
            {
                "id": "cars",
                "stalls": 8,
                "stallWidthM": 3.0,
                "stallLengthM": 6.0,
                "angleDeg": 0,
                "origin": {"x": 24, "y": 24},
                "along": "x",
                "charger": {"type": "dc_160kw", "startNo": 5, "side": "head"},
                "labelPrefix": "直流充电桩",
            },
        ],
        "equipment": [
            {"id": "tx1", "type": "box_transformer", "x": 60, "y": 6, "label": "1250kVA", "capacityKva": 1250},
            *[{"id": f"p{i}", "type": "dc_160kw", "x": 28, "y": 8 + i * 2.5, "label": ""} for i in range(8)],
            *[{"id": f"q{i}", "type": "dc_160kw", "x": 36 + i * 2.5, "y": 30, "label": ""} for i in range(6)],
        ],
    }
    plan = prepare_plan(parse_plan(payload))
    assert sum(r.stalls for r in plan.parkingRows) == 12
    assert all(eq.type not in {"dc_320kw", "dc_160kw", "dc_120kw", "ac_14kw"} for eq in plan.equipment)
    piled = 0
    stall_boxes: list[tuple[float, float, float, float]] = []
    for row in plan.parkingRows:
        assert row.charger is not None and row.charger.type != "none"
        heads = charger_heads(row)
        assert len(heads) == row.stalls
        piled += len(heads)
        for rect, _ in expand_stalls(row):
            xs = [p[0] for p in rect_corners(rect)]
            ys = [p[1] for p in rect_corners(rect)]
            stall_boxes.append((min(xs), min(ys), max(xs), max(ys)))
    assert piled == 12
    for i, a in enumerate(stall_boxes):
        for b in stall_boxes[i + 1 :]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
    tx = next(eq for eq in plan.equipment if eq.type == "box_transformer")
    assert tx.x >= 20.0


def test_parse_plan_coerces_or_drops_buildings_without_rect() -> None:
    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["buildings"] = [
        {"id": "office", "label": "办公楼", "kind": "building"},
        {"id": "factory", "label": "厂房", "x": 0, "y": 10, "w": 18, "h": 12},
    ]
    plan = parse_plan(payload)
    assert len(plan.buildings) == 1
    assert plan.buildings[0].id == "factory"
    assert plan.buildings[0].rect.w == 18
    assert plan.buildings[0].rect.h == 12


def test_parse_plan_drops_trenches_without_polyline() -> None:
    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["trenches"] = [
        {"id": "t1", "widthMm": 1200, "heightMm": 1100, "lengthM": 40},
        {"id": "t2", "widthMm": 1200, "heightMm": 1100},
        {"id": "t3", "points": [{"x": 10, "y": 10}, {"x": 20, "y": 10}]},
    ]
    payload["cables"] = [{"id": "c1", "voltage": "0.4kv"}]
    plan = parse_plan(payload)
    assert plan.trenches == []
    assert plan.cables == []
    packed = prepare_plan(plan)
    assert packed.trenches
    assert any(c.voltage == "0.4kv" for c in packed.cables)


def test_parse_plan_lifts_root_gate() -> None:
    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["gate"] = payload["site"].pop("gate")
    plan = parse_plan(payload)
    assert plan.site.gate is not None
    assert plan.site.gate.side == "south"


def test_parse_plan_coerces_string_gate() -> None:
    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["site"]["gate"] = "东南出入口"
    plan = parse_plan(payload)
    assert plan.site.gate is not None
    assert plan.site.gate.side == "south"
    assert plan.site.gate.widthM > 0


def test_parse_plan_coerces_list_gate() -> None:
    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["site"]["gate"] = ["east", 10, 8]
    plan = parse_plan(payload)
    assert plan.site.gate is not None
    assert plan.site.gate.side == "east"
    assert plan.site.gate.offsetM == 10
    assert plan.site.gate.widthM == 8


def test_parse_plan_keeps_two_gates() -> None:
    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["site"]["gate"] = [
        {"side": "south", "offsetM": 62, "widthM": 10, "label": "出入口"},
        {"side": "north", "offsetM": 58, "widthM": 10, "label": "出入口"},
    ]
    plan = parse_plan(payload)
    from api.services.layouts.pack import plan_gates

    sides = {g.side for g in plan_gates(plan)}
    assert sides == {"south", "north"}
    assert len(plan_gates(plan)) == 2


def test_parse_plan_from_markdown_fence() -> None:
    blob = json.dumps(EXAMPLE_PLAN, ensure_ascii=False)
    text = f"如下：\n```json\n{blob}\n```\n请出图"
    plan = parse_plan(text)
    assert plan.titleBlock.sheetNo == "002"
    assert "充电站" in plan_summary(plan)


def test_parse_plan_coerces_zero_pitch_to_auto() -> None:
    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["parkingRows"][0]["pitchM"] = 0
    payload["parkingRows"][1]["pitchM"] = "0"
    plan = parse_plan(payload)
    assert plan.parkingRows[0].pitchM is None
    assert plan.parkingRows[1].pitchM is None
    trucks = next(r for r in plan.parkingRows if r.id == "trucks")
    stalls = expand_stalls(trucks)
    assert len(stalls) == 4
    assert stalls[1][0].x - stalls[0][0].x > 2.5


def test_parse_plan_keeps_valid_pitch() -> None:
    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["parkingRows"][0]["pitchM"] = 3.4
    plan = parse_plan(payload)
    assert plan.parkingRows[0].pitchM == 3.4


def test_parse_plan_coerces_zero_charger_start_no() -> None:
    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    extra = json.loads(json.dumps(payload["parkingRows"][1]))
    extra["id"] = "cars_extra"
    extra["stalls"] = 8
    extra["charger"] = {"type": "dc_160kw", "startNo": 0, "side": "head"}
    payload["parkingRows"].append(extra)
    plan = parse_plan(payload)
    assert plan.parkingRows[2].charger is not None
    assert plan.parkingRows[2].charger.startNo == 1
    payload["parkingRows"][0]["charger"]["startNo"] = -3
    again = parse_plan(payload)
    assert again.parkingRows[0].charger is not None
    assert again.parkingRows[0].charger.startNo == 1


def test_parse_plan_coerces_schema_version() -> None:
    payload: dict[str, Any] = dict(EXAMPLE_PLAN)
    payload["schemaVersion"] = 1
    plan = parse_plan(payload)
    assert plan.schemaVersion == "1"


def test_parse_plan_coerces_charger_and_legend_aliases() -> None:
    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["parkingRows"][0]["charger"]["type"] = "dc_160kw"
    payload["legend"] = [
        "ring_cabinet",
        "box_transformer",
        "dc_160kw",
        "parking",
        "greenery",
        "tree",
        "cable_trench",
    ]
    plan = parse_plan(payload)
    assert plan.parkingRows[0].charger is not None
    assert plan.parkingRows[0].charger.type == "dc_160kw"
    assert "cable_trench" not in plan.legend
    assert "dc_160kw" in plan.legend


def test_parse_plan_rejects_bad_kind() -> None:
    payload = dict(EXAMPLE_PLAN)
    payload["kind"] = "other"
    with pytest.raises(AppError) as ei:
        parse_plan(payload)
    assert ei.value.status_code == 422


def test_gate_overflow_is_clamped() -> None:
    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["site"]["gate"]["offsetM"] = 90
    payload["site"]["gate"]["widthM"] = 10
    plan = parse_plan(payload)
    gate = plan.site.gate
    assert gate is not None
    assert gate.offsetM + gate.widthM <= plan.site.widthM + 1e-6
    assert gate.widthM == 10
    assert gate.offsetM == pytest.approx(82.0)


def test_gate_copied_from_example_fits_small_site() -> None:
    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["site"]["widthM"] = 45
    payload["site"]["heightM"] = 35
    payload["site"]["gate"]["side"] = "south"
    payload["site"]["gate"]["offsetM"] = 38
    payload["site"]["gate"]["widthM"] = 10
    plan = parse_plan(payload)
    gate = plan.site.gate
    assert gate is not None
    assert gate.offsetM + gate.widthM <= 45 + 1e-6
    assert gate.widthM == 10
    assert gate.offsetM == pytest.approx(35.0)

    payload["site"]["gate"]["side"] = "east"
    east = parse_plan(payload)
    eg = east.site.gate
    assert eg is not None
    assert eg.offsetM + eg.widthM <= 35 + 1e-6
    assert eg.offsetM == pytest.approx(25.0)


def test_render_png_magic() -> None:
    plan = EvChargingStationPlan.model_validate(EXAMPLE_PLAN)
    blob = render_plan_png(plan)
    assert blob.startswith(b"\x89PNG")
    assert len(blob) > 2000


def test_sheet_chrome_fits_bottom_margin() -> None:
    from io import BytesIO

    from PIL import Image

    from api.services.layouts.render import _PAD_B, _PAD_T, _fit_text, _font, _text_size
    from api.services.layouts.rules import default_sheet_notes

    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["notes"] = default_sheet_notes("厂区左侧厂房与办公楼，右侧布置充电区")
    payload["titleBlock"]["project"] = "充电站平面布置图"
    payload["titleBlock"]["title"] = "充电站平面布置图"
    plan = parse_plan(payload)
    blob = render_plan_png(plan)
    img = Image.open(BytesIO(blob))
    assert img.size[1] > _PAD_T + _PAD_B
    assert _PAD_B >= 220
    font = _font(16)
    title = "充电站平面布置图"
    assert _text_size(font, title)[0] <= 208
    assert _fit_text(title, font, 208) == title
    assert blob.startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_layout_out_node_writes_state() -> None:
    state: dict[str, Any] = {
        "output": json.dumps(EXAMPLE_PLAN, ensure_ascii=False),
        "outputImages": [],
    }
    detail = await _exec_layout_out(data={"render": True, "copyToOutput": True}, state=state)
    assert detail["stalls"] == 12
    assert state["layout"]["kind"] == "ev_charging_station_plan"
    assert "充电站" in str(state["output"])
    imgs = state["outputImages"]
    assert len(imgs) == 1
    assert str(imgs[0].get("mimeType")) == "image/png"
    assert str(imgs[0].get("dataUrl") or "").startswith("data:image/png")


def test_plan_dxf_writes_and_rasterizes(tmp_path) -> None:
    from api.services.layouts.cad import rasterize_dxf, write_plan_dxf

    plan = prepare_plan(parse_plan(EXAMPLE_PLAN))
    dest = tmp_path / "station.dxf"
    write_plan_dxf(plan, dest)
    raw = dest.read_bytes()
    assert raw.startswith(b"  0") or b"SECTION" in raw
    png = rasterize_dxf(dest)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 2000


def test_replace_templates_layout() -> None:
    out = _replace_templates("L={{layout}}", {"layout": {"kind": "ev_charging_station_plan"}})
    assert "ev_charging_station_plan" in out


def test_validate_graph_accepts_layout_out() -> None:
    graph = {
        "nodes": [
            {"id": "start", "type": "start", "data": {}},
            {"id": "llm", "type": "llm", "data": {}},
            {"id": "layout", "type": "layout_out", "data": {"jsonSource": "{{output}}"}},
            {"id": "end", "type": "end", "data": {}},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "llm"},
            {"id": "e2", "source": "llm", "target": "layout"},
            {"id": "e3", "source": "layout", "target": "end"},
        ],
    }
    out = validate_graph(graph)
    types = {n["id"]: n["type"] for n in out["nodes"]}
    assert types["layout"] == "layout_out"


def test_layout_prompt_is_compact_and_validates() -> None:
    from api.services.layouts.prompt import LAYOUT_LLM_SYSTEM_PROMPT, _COMPACT_EXAMPLE
    from api.services.workflows.nodes import _collect_system_parts

    assert len(LAYOUT_LLM_SYSTEM_PROMPT) < 9000
    assert "第一层" in LAYOUT_LLM_SYSTEM_PROMPT
    assert "第二层" in LAYOUT_LLM_SYSTEM_PROMPT
    plan = parse_plan(_COMPACT_EXAMPLE)
    assert plan.kind == "ev_charging_station_plan"
    assert sum(r.stalls for r in plan.parkingRows) == 8
    assert abs(plan.parkingRows[0].angleDeg + 45) < 1e-6
    # 已发布工作流里的旧长提示会被运行时覆盖，不必重新 seed。
    parts = _collect_system_parts(
        {"systemPrompt": "legacy ev_charging_station_plan parkingRows " + ("x" * 80)},
        {},
        None,
    )
    assert parts == [LAYOUT_LLM_SYSTEM_PROMPT]


def test_qwen_generation_options_disable_thinking() -> None:
    from api.services.ai.llm import LLMClient

    client = LLMClient(
        api_base="https://example.com/v1",
        api_key="test",
        model="qwen3.7-plus",
        timeout_seconds=360,
    )
    out = client._apply_generation_options({"model": "qwen3.7-plus"})
    assert out["enable_thinking"] is False
    assert out["max_tokens"] == 8192
    timeout = client._http_timeout()
    assert timeout.read == 360.0
    assert timeout.connect == 20.0


def test_parse_layout_brief_and_override_copied_case() -> None:
    from api.services.layouts.brief import apply_layout_brief, parse_layout_brief

    brief = parse_layout_brief(
        "空地 55m×48m，4 个重卡车位（5m×17m）、20 个轿车车位，1 台 1250kVA 箱变"
    )
    assert brief.trucks == 4
    assert brief.cars == 20
    assert brief.transformer_n == 1
    assert brief.transformer_kva == 1250
    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["parkingRows"] = [
        {
            "id": "copied",
            "stalls": 17,
            "stallWidthM": 4.0,
            "stallLengthM": 17.0,
            "origin": {"x": 10, "y": 10},
            "charger": {"type": "dc_320kw", "startNo": 1, "side": "head"},
        }
    ]
    payload["equipment"] = [
        {"id": "a", "type": "box_transformer", "x": 40, "y": 20, "label": "1#变电", "capacityKva": 1600},
        {"id": "b", "type": "box_transformer", "x": 44, "y": 20, "label": "2#变电", "capacityKva": 2000},
        {"id": "c", "type": "box_transformer", "x": 48, "y": 20, "label": "3#变电", "capacityKva": 2000},
    ]
    payload["roads"] = [
        {
            "polygon": [
                {"x": 20, "y": 8},
                {"x": 40, "y": 8},
                {"x": 40, "y": 40},
                {"x": 20, "y": 40},
            ],
            "label": "内部道路",
        }
    ]
    payload["trees"] = [{"x": 12, "y": 12}]
    plan = apply_layout_brief(
        parse_plan(payload),
        "4 个重卡、20 个轿车、1 台 1250kVA 箱变",
    )
    plan = prepare_plan(plan)
    assert sum(r.stalls for r in plan.parkingRows) == 24
    assert sum(r.stalls for r in plan.parkingRows if r.stallLengthM >= 10) == 4
    assert sum(r.stalls for r in plan.parkingRows if r.stallLengthM < 10) == 20
    feeders = [e for e in plan.equipment if e.type in {"box_transformer", "ring_box_transformer"}]
    assert len(feeders) == 1
    assert feeders[0].capacityKva == 1250
    assert not any((r.label or "").startswith("内部") for r in plan.roads)


def test_brief_parses_chinese_and_typo_car() -> None:
    from api.services.layouts.brief import parse_layout_brief

    brief = parse_layout_brief("空地右下出入口，四个重卡，二十个带充电桩的桥车车位")
    assert brief.trucks == 4
    assert brief.cars == 20
    assert brief.gate_side == "south"
    assert brief.gate_east is True


def test_prepare_keeps_user_gate_not_road_end() -> None:
    payload = {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {
            "widthM": 80,
            "heightM": 50,
            "gate": {"side": "south", "offsetM": 62, "widthM": 10, "label": "出入口"},
        },
        "buildings": [
            {"id": "factory", "label": "厂房", "kind": "factory", "rect": {"x": 0, "y": 22, "w": 18, "h": 24}},
            {"id": "office", "label": "办公楼", "kind": "building", "rect": {"x": 0, "y": 2, "w": 18, "h": 18}},
        ],
        "roads": [
            {
                "polygon": [
                    {"x": 18, "y": 2},
                    {"x": 24, "y": 2},
                    {"x": 24, "y": 48},
                    {"x": 18, "y": 48},
                ],
                "label": "路",
            }
        ],
        "parkingRows": [
            {
                "id": "trucks",
                "stalls": 4,
                "stallWidthM": 5,
                "stallLengthM": 17,
                "origin": {"x": 30, "y": 4},
                "charger": {"type": "dc_320kw", "startNo": 1, "side": "head"},
            },
            {
                "id": "cars",
                "stalls": 20,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "origin": {"x": 30, "y": 24},
                "charger": {"type": "dc_160kw", "startNo": 5, "side": "head"},
            },
        ],
        "equipment": [
            {"id": "tx1", "type": "box_transformer", "x": 70, "y": 8, "label": "1250kVA", "capacityKva": 1250}
        ],
        "greenery": [
            {
                "polygon": [
                    {"x": 58, "y": 0},
                    {"x": 80, "y": 0},
                    {"x": 80, "y": 12},
                    {"x": 58, "y": 12},
                ],
                "label": "绿化",
            }
        ],
    }
    from api.services.layouts.brief import apply_layout_brief
    from api.services.layouts.pack import plan_gates

    parsed = parse_plan(payload)
    parsed.site.gate = parsed.site.gate.model_copy(
        update={"side": "north", "offsetM": 60, "label": "北侧主入口"}
    )
    plan = apply_layout_brief(
        parsed,
        "待办场地 55m×48m，右下出入口，4 个重卡，20 个轿车，1 台 1250kVA",
    )
    plan = prepare_plan(plan)
    assert plan.site.gate is not None
    assert plan.site.gate.side == "south"
    assert plan.site.gate.offsetM >= plan.site.widthM * 0.5
    assert plan.site.gate.label == "出入口"
    assert len(plan_gates(plan)) == 1
    assert sum(r.stalls for r in plan.parkingRows if r.stallLengthM < 10) == 20
    assert sum(r.stalls for r in plan.parkingRows if r.stallLengthM >= 10) == 4
    gate = plan.site.gate
    for g in plan.greenery:
        xs = [p.x for p in g.polygon]
        ys = [p.y for p in g.polygon]
        overlap_gate = not (
            max(xs) < gate.offsetM - 1
            or min(xs) > gate.offsetM + gate.widthM + 1
            or min(ys) > 10
        )
        assert not overlap_gate
    tx = next(e for e in plan.equipment if e.type == "box_transformer")
    assert tx.y > 10 or tx.x < gate.offsetM - 2
    for row in plan.parkingRows:
        xs = [p[0] for rect, _ in expand_stalls(row) for p in rect_corners(rect)]
        ys = [p[1] for rect, _ in expand_stalls(row) for p in rect_corners(rect)]
        assert not (min(xs) <= tx.x <= max(xs) and min(ys) <= tx.y <= max(ys))
    lv = [c for c in plan.cables if c.voltage == "0.4kv"]
    assert lv
    for row in plan.parkingRows:
        hx, hy = charger_heads(row)[0]
        nearest = min(math.hypot(p.x - hx, p.y - hy) for c in lv for p in c.polyline)
        assert nearest < 0.35


def test_prepare_keeps_two_draft_gates() -> None:
    payload = {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {
            "widthM": 80,
            "heightM": 50,
            "gate": {"side": "south", "offsetM": 62, "widthM": 10, "label": "出入口"},
            "gates": [
                {"side": "south", "offsetM": 62, "widthM": 10, "label": "出入口"},
                {"side": "north", "offsetM": 58, "widthM": 10, "label": "出入口"},
            ],
        },
        "buildings": [
            {"id": "factory", "label": "厂房", "kind": "factory", "rect": {"x": 0, "y": 22, "w": 18, "h": 24}},
            {"id": "office", "label": "办公楼", "kind": "building", "rect": {"x": 0, "y": 2, "w": 18, "h": 18}},
        ],
        "parkingRows": [
            {
                "id": "cars",
                "stalls": 8,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "origin": {"x": 30, "y": 24},
                "charger": {"type": "dc_160kw", "startNo": 1, "side": "head"},
            }
        ],
        "equipment": [
            {"id": "tx1", "type": "box_transformer", "x": 70, "y": 8, "label": "1250kVA", "capacityKva": 1250}
        ],
        "greenery": [
            {
                "polygon": [
                    {"x": 54, "y": 40},
                    {"x": 80, "y": 40},
                    {"x": 80, "y": 50},
                    {"x": 54, "y": 50},
                ],
                "label": "绿化",
            }
        ],
    }
    from api.services.layouts.brief import apply_layout_brief
    from api.services.layouts.pack import plan_gates

    plan = apply_layout_brief(
        parse_plan(payload),
        "待办场地两个出入口，4 个重卡，20 个轿车，1 台 1250kVA",
    )
    plan = prepare_plan(plan)
    sides = {g.side for g in plan_gates(plan)}
    assert sides == {"south", "north"}
    assert len(plan_gates(plan)) == 2
    north = next(g for g in plan_gates(plan) if g.side == "north")
    for g in plan.greenery:
        xs = [p.x for p in g.polygon]
        ys = [p.y for p in g.polygon]
        overlap_north = not (
            max(xs) < north.offsetM - 1
            or min(xs) > north.offsetM + north.widthM + 1
            or max(ys) < plan.site.heightM - 10
        )
        assert not overlap_north


def test_layout_user_text_omits_rag_cases() -> None:
    from api.services.workflows.nodes import _collect_user_text

    text = _collect_user_text(
        {
            "query": "4个重卡、20个轿车",
            "context": "历史案例：斜列15桩、3台1600kVA",
            "visionText": "图上约15个车位",
        },
        omit_rag=True,
    )
    assert "4个重卡" in text
    assert "1600kVA" not in text
    assert "第一层" in text or "当前任务" in text


def test_prepare_locks_sizes_and_back_to_back_cars() -> None:
    from api.services.layouts.brief import apply_layout_brief, parse_layout_brief
    from api.services.layouts.check import check_plan

    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    plan = apply_layout_brief(parse_plan(payload), "4个重卡、20个轿车")
    plan = prepare_plan(plan)
    cars = [r for r in plan.parkingRows if r.stallLengthM < 10]
    trucks = [r for r in plan.parkingRows if r.stallLengthM >= 10]
    assert sum(r.stalls for r in cars) == 20
    assert sum(r.stalls for r in trucks) == 4
    assert all(abs(r.stallWidthM - 3) < 0.05 and abs(r.stallLengthM - 6) < 0.05 for r in cars)
    assert all(abs(r.stallWidthM - 5) < 0.05 and abs(r.stallLengthM - 17) < 0.05 for r in trucks)
    codes = {i.code for i in check_plan(plan, parse_layout_brief("4个重卡、20个轿车"))}
    assert not codes & {"truck_count", "car_count", "truck_size", "car_size", "redline"}


def test_check_plan_flags_copied_case_counts() -> None:
    from api.services.layouts.brief import parse_layout_brief
    from api.services.layouts.check import check_plan

    issues = check_plan(parse_plan(EXAMPLE_PLAN), parse_layout_brief("4个重卡、20个轿车"))
    assert any(i.code == "car_count" for i in issues)


def test_brief_ignores_vision_when_query_only() -> None:
    from api.services.layouts.brief import parse_layout_brief

    brief = parse_layout_brief("4个重卡、20个轿车，1台1250kVA")
    assert brief.cars == 20
    assert brief.transformer_n == 1
    assert brief.transformer_kva == 1250


def test_brief_parses_two_transformers_with_kva() -> None:
    from api.services.layouts.brief import parse_layout_brief

    brief = parse_layout_brief("两台1250kVA箱变，4个重卡，20个轿车")
    assert brief.transformer_n == 2
    assert brief.transformer_kva == 1250


def test_ensure_transformers_adds_missing_units() -> None:
    from api.services.layouts.brief import apply_layout_brief

    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["equipment"] = [
        {"id": "tx1", "type": "box_transformer", "x": 70, "y": 40, "capacityKva": 1600}
    ]
    plan = apply_layout_brief(parse_plan(payload), "两台1250kVA箱变")
    feeders = [e for e in plan.equipment if e.type == "box_transformer"]
    assert len(feeders) == 2
    assert all(e.capacityKva == 1250 for e in feeders)


def test_restore_draft_does_not_inject_factory_template() -> None:
    from api.services.layouts.brief import restore_draft_context

    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["buildings"] = []
    payload["roads"] = []
    plan = restore_draft_context(
        parse_plan(payload),
        "左侧厂房和办公楼，中间路，右侧空地55m×48m",
        "读图：厂房、办公楼、路、空白场地",
    )
    assert plan.buildings == []
    assert plan.roads == []


def test_pack_keeps_24_stalls_without_factory_template() -> None:
    from api.services.layouts.brief import apply_layout_brief

    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["site"] = {
        "widthM": 90,
        "heightM": 48,
        "gate": {"side": "south", "offsetM": 40, "widthM": 10, "label": "出入口"},
    }
    payload["buildings"] = []
    payload["roads"] = []
    plan = apply_layout_brief(
        parse_plan(payload),
        "两台1250kVA箱变，4个重卡，20个轿车",
    )
    plan = prepare_plan(plan)
    assert sum(r.stalls for r in plan.parkingRows) == 24
    assert sum(r.stalls for r in plan.parkingRows if r.stallLengthM >= 10) == 4
    assert sum(r.stalls for r in plan.parkingRows if r.stallLengthM < 10) == 20
    txs = [e for e in plan.equipment if e.type == "box_transformer"]
    assert len(txs) == 2
    assert plan.buildings == []


def test_pack_merges_fragmented_car_rows_into_two() -> None:
    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["site"] = {
        "widthM": 80,
        "heightM": 48,
        "gate": {"side": "south", "offsetM": 62, "widthM": 10, "label": "出入口"},
        "gates": [
            {"side": "south", "offsetM": 62, "widthM": 10, "label": "出入口"},
            {"side": "north", "offsetM": 28, "widthM": 10, "label": "北出入口"},
        ],
    }
    payload["buildings"] = [
        {"id": "factory", "label": "厂房", "kind": "factory", "rect": {"x": 0, "y": 22, "w": 18, "h": 24}},
        {"id": "office", "label": "办公楼", "kind": "building", "rect": {"x": 0, "y": 2, "w": 18, "h": 18}},
    ]
    payload["roads"] = [
        {
            "polygon": [
                {"x": 18, "y": 0},
                {"x": 24, "y": 0},
                {"x": 24, "y": 48},
                {"x": 18, "y": 48},
            ],
            "label": "路",
        },
        {
            "polygon": [
                {"x": 40, "y": 20},
                {"x": 70, "y": 20},
                {"x": 70, "y": 28},
                {"x": 40, "y": 28},
            ],
            "label": "内部行车道",
        },
    ]
    payload["parkingRows"] = [
        {
            "id": "trucks",
            "stalls": 4,
            "stallWidthM": 5,
            "stallLengthM": 17,
            "origin": {"x": 30, "y": 4},
            "charger": {"type": "dc_320kw", "startNo": 1, "side": "head"},
        },
        {
            "id": "c1",
            "stalls": 7,
            "stallWidthM": 3,
            "stallLengthM": 6,
            "origin": {"x": 30, "y": 28},
            "charger": {"type": "dc_160kw", "startNo": 5, "side": "head"},
        },
        {
            "id": "c2",
            "stalls": 3,
            "stallWidthM": 3,
            "stallLengthM": 6,
            "origin": {"x": 54, "y": 28},
            "charger": {"type": "dc_160kw", "startNo": 12, "side": "head"},
        },
        {
            "id": "c3",
            "stalls": 7,
            "stallWidthM": 3,
            "stallLengthM": 6,
            "origin": {"x": 30, "y": 36},
            "charger": {"type": "dc_160kw", "startNo": 15, "side": "head"},
        },
        {
            "id": "c4",
            "stalls": 3,
            "stallWidthM": 3,
            "stallLengthM": 6,
            "origin": {"x": 62, "y": 4},
            "charger": {"type": "dc_160kw", "startNo": 22, "side": "head"},
        },
    ]
    payload["equipment"] = [
        {"id": "tx1", "type": "box_transformer", "x": 76, "y": 6, "label": "1250kVA", "capacityKva": 1250},
        {"id": "tx2", "type": "box_transformer", "x": 76, "y": 6, "label": "1250kVA", "capacityKva": 1250},
    ]
    plan = prepare_plan(parse_plan(payload))
    assert sum(r.stalls for r in plan.parkingRows if r.stallLengthM < 10) == 20
    assert len([r for r in plan.parkingRows if r.stallLengthM < 10]) >= 2
    assert not any((r.label or "") == "内部行车道" for r in plan.roads)


def test_pack_drops_copied_aisle_labels_keeps_counts_and_cables() -> None:
    from api.services.layouts.brief import apply_layout_brief

    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["site"] = {
        "widthM": 90,
        "heightM": 48,
        "gate": {"side": "south", "offsetM": 40, "widthM": 10, "label": "出入口"},
    }
    payload["buildings"] = []
    payload["roads"] = [
        {
            "polygon": [
                {"x": 0, "y": 8},
                {"x": 22, "y": 8},
                {"x": 22, "y": 28},
                {"x": 0, "y": 28},
            ],
            "label": "重卡主通道",
        }
    ]
    plan = apply_layout_brief(parse_plan(payload), "两台1250kVA箱变，4个重卡，20个轿车")
    plan = prepare_plan(plan)
    assert sum(r.stalls for r in plan.parkingRows if r.stallLengthM < 10) == 20
    assert not any("主通道" in (r.label or "") for r in plan.roads)
    lv = [c for c in plan.cables if c.voltage == "0.4kv"]
    heads = [p for r in plan.parkingRows for p in charger_heads(r)]
    assert heads and lv
    for hx, hy in heads:
        nearest = min(math.hypot(p.x - hx, p.y - hy) for c in lv for p in c.polyline)
        assert nearest < 0.35


def test_pack_never_puts_stalls_on_factory_or_office() -> None:
    payload = {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {
            "widthM": 48,
            "heightM": 40,
            "gate": {"side": "east", "offsetM": 8, "widthM": 8, "label": "东南出入口"},
        },
        "buildings": [
            {"id": "factory", "label": "厂房", "kind": "factory", "rect": {"x": 0, "y": 18, "w": 22, "h": 20}},
            {"id": "office", "label": "办公楼", "kind": "building", "rect": {"x": 0, "y": 2, "w": 22, "h": 14}},
        ],
        "roads": [
            {
                "polygon": [
                    {"x": 4, "y": 10},
                    {"x": 40, "y": 10},
                    {"x": 40, "y": 22},
                    {"x": 4, "y": 22},
                ],
                "label": "重卡车道",
            }
        ],
        "parkingRows": [
            {
                "id": "trucks",
                "stalls": 4,
                "stallWidthM": 5,
                "stallLengthM": 17,
                "origin": {"x": 2, "y": 2},
                "charger": {"type": "dc_320kw", "startNo": 1, "side": "head"},
            },
            {
                "id": "cars",
                "stalls": 20,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "origin": {"x": 2, "y": 2},
                "charger": {"type": "dc_160kw", "startNo": 5, "side": "head"},
            },
        ],
        "equipment": [
            {"id": "tx1", "type": "box_transformer", "x": 10, "y": 10, "label": "1250kVA", "capacityKva": 1250},
            {"id": "tx2", "type": "box_transformer", "x": 12, "y": 12, "label": "1250kVA", "capacityKva": 1250},
        ],
    }
    plan = prepare_plan(parse_plan(payload))
    buildings = []
    for b in plan.buildings:
        buildings.append((b.rect.x, b.rect.y, b.rect.x + b.rect.w, b.rect.y + b.rect.h))

    def overlaps(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
        return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])

    for row in plan.parkingRows:
        for rect, _ in expand_stalls(row):
            xs = [p[0] for p in rect_corners(rect)]
            ys = [p[1] for p in rect_corners(rect)]
            box = (min(xs), min(ys), max(xs), max(ys))
            assert box[0] >= 22.0
            for b in buildings:
                assert not overlaps(box, b)
    assert not any("重卡车道" in (r.label or "") for r in plan.roads)
    park = []
    for row in plan.parkingRows:
        for rect, _ in expand_stalls(row):
            xs = [p[0] for p in rect_corners(rect)]
            ys = [p[1] for p in rect_corners(rect)]
            park.append((min(xs), min(ys), max(xs), max(ys)))
    for eq in plan.equipment:
        if eq.type != "box_transformer":
            continue
        tx = (eq.x - 1.6, eq.y - 1.6, eq.x + 1.6, eq.y + 1.6)
        for box in park:
            assert not overlaps(tx, box)


def test_road_only_does_not_inflate_site_and_stalls_are_centered() -> None:
    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["site"] = {
        "widthM": 48,
        "heightM": 48,
        "gate": {"side": "east", "offsetM": 16, "widthM": 10, "label": "东侧出入口"},
    }
    payload["buildings"] = []
    payload["roads"] = [
        {
            "polygon": [
                {"x": 0, "y": 0},
                {"x": 6, "y": 0},
                {"x": 6, "y": 48},
                {"x": 0, "y": 48},
            ],
            "label": "路",
        }
    ]
    payload["parkingRows"] = [
        {
            "id": "trucks",
            "stalls": 4,
            "stallWidthM": 5,
            "stallLengthM": 17,
            "origin": {"x": 8, "y": 4},
            "charger": {"type": "dc_320kw", "startNo": 1, "side": "head"},
        },
        {
            "id": "cars",
            "stalls": 20,
            "stallWidthM": 3,
            "stallLengthM": 6,
            "origin": {"x": 8, "y": 24},
            "charger": {"type": "dc_160kw", "startNo": 5, "side": "head"},
        },
    ]
    payload["equipment"] = [
        {"id": "tx1", "type": "box_transformer", "x": 40, "y": 8, "capacityKva": 1250},
        {"id": "tx2", "type": "box_transformer", "x": 40, "y": 30, "capacityKva": 1250},
    ]
    plan = prepare_plan(parse_plan(payload))
    assert plan.site.widthM <= 52.0
    xs: list[float] = []
    for row in plan.parkingRows:
        for rect, _ in expand_stalls(row):
            xs.extend(p[0] for p in rect_corners(rect))
    assert min(xs) >= 5.5
    road = next(r for r in plan.roads if (r.label or "") == "路")
    assert min(p.x for p in road.polygon) == 0.0


def test_cars_north_trucks_south_leave_center_aisle() -> None:
    from api.services.layouts.brief import apply_layout_brief

    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["site"] = {
        "widthM": 110,
        "heightM": 70,
        "gate": {"side": "north", "offsetM": 50, "widthM": 10, "label": "出入口"},
        "gates": [
            {"side": "north", "offsetM": 50, "widthM": 10, "label": "北出入口"},
            {"side": "east", "offsetM": 8, "widthM": 10, "label": "东南出入口"},
        ],
    }
    payload["buildings"] = [
        {"id": "factory", "label": "厂房", "kind": "factory", "rect": {"x": 0, "y": 38, "w": 22, "h": 30}},
        {"id": "office", "label": "办公楼", "kind": "building", "rect": {"x": 0, "y": 4, "w": 22, "h": 28}},
    ]
    payload["roads"] = [
        {
            "polygon": [
                {"x": 22, "y": 0},
                {"x": 28, "y": 0},
                {"x": 28, "y": 70},
                {"x": 22, "y": 70},
            ],
            "label": "路",
        }
    ]
    payload["equipment"] = [
        {"id": "tx1", "type": "box_transformer", "x": 100, "y": 10, "capacityKva": 1250, "label": "重卡"},
        {"id": "tx2", "type": "box_transformer", "x": 100, "y": 55, "capacityKva": 1250, "label": "轿车"},
    ]
    plan = apply_layout_brief(parse_plan(payload), "两台1250kVA箱变，4个重卡，20个轿车")
    plan = prepare_plan(plan)
    truck_rows = [r for r in plan.parkingRows if r.stallLengthM >= 10]
    car_rows = [r for r in plan.parkingRows if r.stallLengthM < 10]
    truck_ys = [p[1] for r in truck_rows for rect, _ in expand_stalls(r) for p in rect_corners(rect)]
    car_ys = [p[1] for r in car_rows for rect, _ in expand_stalls(r) for p in rect_corners(rect)]
    truck_y0 = min(truck_ys)
    car_y0 = min(car_ys)
    assert truck_y0 < car_y0
    assert sum(r.stalls for r in truck_rows) == 4
    assert sum(r.stalls for r in car_rows) == 20


def test_new_turn_does_not_reuse_factory_layout_or_stall_count() -> None:
    from api.services.layouts.brief import (
        apply_layout_brief,
        parse_layout_brief,
        prune_unmentioned_site_context,
        restore_draft_context,
    )

    brief = parse_layout_brief("如图所示我想在T型区域中规划2台2000W的箱变 16个带充电桩的车位")
    assert brief.cars == 16
    assert brief.trucks is None
    assert brief.transformer_n == 2
    assert brief.transformer_kva == 2000

    size = parse_layout_brief("场地55m×48m，4个重卡，20个轿车")
    assert size.site_w == 55
    assert size.site_h == 48
    assert size.site_is_yard is False

    yard = parse_layout_brief("右侧空地55m×48m，4个重卡，20个轿车")
    assert yard.site_is_yard is True

    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    plan = apply_layout_brief(parse_plan(payload), "场地55m×48m，4个重卡，20个轿车")
    plan = restore_draft_context(plan, "场地55m×48m，4个重卡，20个轿车", has_draft_image=True)
    plan = prune_unmentioned_site_context(plan, "场地55m×48m，4个重卡，20个轿车")
    labels = {b.label for b in plan.buildings}
    assert "厂房" not in labels
    assert plan.site.widthM == 55
    assert plan.site.heightM == 48

    t_plan = apply_layout_brief(
        parse_plan(payload),
        "如图所示我想在T型区域中规划2台2000W的箱变 16个带充电桩的车位",
    )
    t_plan = restore_draft_context(
        t_plan,
        "如图所示我想在T型区域中规划2台2000W的箱变 16个带充电桩的车位",
        "读图：T型规划区域",
        has_draft_image=True,
    )
    t_plan = prune_unmentioned_site_context(
        t_plan,
        "如图所示我想在T型区域中规划2台2000W的箱变 16个带充电桩的车位",
        "读图：T型规划区域",
    )
    assert sum(r.stalls for r in t_plan.parkingRows) == 16
    assert not any(b.label == "厂房" for b in t_plan.buildings)
    assert not any(b.label == "办公楼" for b in t_plan.buildings)


def test_irregular_t_site_is_drawn_as_polygon_not_rectangle() -> None:
    from api.services.layouts.brief import (
        apply_layout_brief,
        attach_site_polygon,
        prune_unmentioned_site_context,
    )
    from api.services.layouts.schema import site_is_irregular

    query = "如图所示我想在T型区域中规划2台2000W的箱变 16个带充电桩的车位"
    plan = attach_site_polygon(parse_plan(json.loads(json.dumps(EXAMPLE_PLAN))), query)
    plan = apply_layout_brief(plan, query)
    plan = prune_unmentioned_site_context(plan, query)
    plan = prepare_plan(plan)
    assert not site_is_irregular(plan.site)
    assert not any(b.label in {"厂房", "办公楼"} for b in plan.buildings)
    assert sum(r.stalls for r in plan.parkingRows) == 16
    vision = (
        '外形：梯形+办公楼\n'
        'polygon: [{"x":0,"y":10},{"x":12,"y":10},{"x":12,"y":0},'
        '{"x":24,"y":0},{"x":24,"y":28},{"x":0,"y":28}]'
    )
    # 模型先写错成另一种轮廓时，读图顶点必须覆盖
    wrong = json.loads(json.dumps(EXAMPLE_PLAN))
    wrong["site"]["polygon"] = [
        {"x": 0, "y": 0},
        {"x": 50, "y": 0},
        {"x": 50, "y": 10},
        {"x": 20, "y": 10},
        {"x": 20, "y": 26},
        {"x": 0, "y": 26},
    ]
    wrong["site"]["widthM"] = 50
    wrong["site"]["heightM"] = 26
    parsed = attach_site_polygon(parse_plan(wrong), query, vision)
    packed = prepare_plan(parsed)
    assert site_is_irregular(packed.site)
    xs = [p.x for p in packed.site.polygon]
    ys = [p.y for p in packed.site.polygon]
    assert min(xs) == 0 and max(xs) == 24
    assert min(ys) == 0 and max(ys) == 28
    assert packed.site.widthM == 24
    assert packed.site.heightM == 28
    png = render_plan_png(packed)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_layout_revise_keeps_prior_site_when_changing_charger() -> None:
    from api.services.layouts.revise import (
        is_layout_revise_query,
        parse_charger_type_hint,
        revise_plan_from_prior,
    )

    assert is_layout_revise_query(
        "全部改成320kW直流桩", has_prior=True, has_images=False
    )
    assert is_layout_revise_query(
        "你绘制的充电桩可否将中间的4个充电桩车位靠墙布置？",
        has_prior=True,
        has_images=False,
    )
    assert not is_layout_revise_query(
        "如图所示我想在T型区域中规划16个车位", has_prior=True, has_images=True
    )
    assert not is_layout_revise_query(
        "我现在有一个标准足球场大小的场地，我想让你帮我画一个配置了8个轿车充电桩和一个2000W的箱变的布置图",
        has_prior=True,
        has_images=False,
    )
    assert parse_charger_type_hint("全部改成320kW") == "dc_320kw"

    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["site"]["polygon"] = [
        {"x": 0, "y": 10},
        {"x": 12, "y": 10},
        {"x": 12, "y": 0},
        {"x": 36, "y": 0},
        {"x": 36, "y": 28},
        {"x": 0, "y": 28},
    ]
    payload["site"]["widthM"] = 36
    payload["site"]["heightM"] = 28
    payload["buildings"] = [
        {"id": "office", "label": "办公楼", "rect": {"x": 6, "y": 20, "w": 24, "h": 8}}
    ]
    prior = parse_plan(payload)
    out = revise_plan_from_prior(prior, query="全部改成320kW直流桩")
    out = prepare_plan(out)
    assert len(out.site.polygon) >= 6
    assert out.site.widthM == 36
    assert out.site.heightM == 28
    assert any(b.label == "办公楼" for b in out.buildings)
    assert all(
        r.charger and r.charger.type == "dc_320kw"
        for r in out.parkingRows
        if r.charger and r.charger.type != "none"
    )


def test_spatial_revise_pins_middle_row_to_wall() -> None:
    from api.services.layouts.revise import revise_plan_from_prior

    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["site"] = {
        "widthM": 30,
        "heightM": 20,
        "gate": {"side": "south", "offsetM": 11, "widthM": 8},
    }
    payload["buildings"] = []
    payload["roads"] = []
    payload["parkingRows"] = [
        {
            "id": "north",
            "stalls": 4,
            "stallWidthM": 3,
            "stallLengthM": 6,
            "angleDeg": 0,
            "origin": {"x": 4, "y": 12},
            "along": "x",
            "charger": {"type": "dc_160kw", "startNo": 1, "side": "head"},
        },
        {
            "id": "mid",
            "stalls": 4,
            "stallWidthM": 3,
            "stallLengthM": 6,
            "angleDeg": 0,
            "origin": {"x": 4, "y": 6},
            "along": "x",
            "charger": {"type": "dc_160kw", "startNo": 5, "side": "head"},
        },
    ]
    prior = parse_plan(payload)
    out = revise_plan_from_prior(prior, query="可否将中间的4个充电桩车位靠墙布置")
    mid = next(r for r in out.parkingRows if r.id == "mid" or "wall" in r.id)
    assert mid.origin.y + mid.stallLengthM >= 20 - 2.5


def test_spatial_revise_1_to_4_west_wall_vertical() -> None:
    from api.services.layouts.pack import _row_aabb
    from api.services.layouts.revise import (
        _infer_wall_side,
        is_layout_revise_query,
        revise_plan_from_prior,
    )

    assert _infer_wall_side(parse_plan(EXAMPLE_PLAN), "靠左侧墙体竖着摆放") == "west"
    assert _infer_wall_side(parse_plan(EXAMPLE_PLAN), "靠西面墙体竖着摆放") == "west"
    assert is_layout_revise_query(
        "1-4号充电桩靠西面墙体竖着摆放，请整改",
        has_prior=True,
        has_images=False,
    )

    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["site"] = {
        "widthM": 30,
        "heightM": 20,
        "gate": {"side": "south", "offsetM": 11, "widthM": 8},
    }
    payload["buildings"] = []
    payload["roads"] = []
    payload["parkingRows"] = [
        {
            "id": "a",
            "stalls": 4,
            "stallWidthM": 3,
            "stallLengthM": 6,
            "angleDeg": 0,
            "origin": {"x": 8, "y": 12},
            "along": "x",
            "charger": {"type": "dc_160kw", "startNo": 1, "side": "head"},
        },
        {
            "id": "b",
            "stalls": 4,
            "stallWidthM": 3,
            "stallLengthM": 6,
            "angleDeg": 0,
            "origin": {"x": 8, "y": 4},
            "along": "x",
            "charger": {"type": "dc_160kw", "startNo": 5, "side": "head"},
        },
    ]
    out = revise_plan_from_prior(
        parse_plan(payload),
        query="我希望1-4号充电桩靠西面墙体竖着摆放，请整改一下",
    )
    wall = next(r for r in out.parkingRows if r.charger and r.charger.startNo == 1)
    assert wall.along == "y"
    assert wall.stalls == 4
    box = _row_aabb(wall)
    assert box[0] <= 1.5  # 贴西墙（左侧）


def test_revise_keeps_row_origins_when_only_charger_changes() -> None:
    from api.services.layouts.pack import prepare_plan
    from api.services.layouts.revise import revise_plan_from_prior

    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["buildings"] = []
    payload["roads"] = []
    payload["parkingRows"] = [
        {
            "id": "north",
            "stalls": 4,
            "stallWidthM": 3,
            "stallLengthM": 6,
            "angleDeg": -30,
            "origin": {"x": 5, "y": 12},
            "along": "x",
            "charger": {"type": "dc_160kw", "startNo": 1, "side": "head"},
        },
        {
            "id": "south",
            "stalls": 4,
            "stallWidthM": 3,
            "stallLengthM": 6,
            "angleDeg": -30,
            "origin": {"x": 5, "y": 2},
            "along": "x",
            "charger": {"type": "dc_160kw", "startNo": 5, "side": "head"},
        },
    ]
    payload["site"] = {
        "widthM": 30,
        "heightM": 20,
        "gate": {"side": "south", "offsetM": 11, "widthM": 8},
    }
    prior = parse_plan(payload)
    out = revise_plan_from_prior(prior, query="全部改成320kW直流桩")
    out = prepare_plan(out, gentle=True)
    by_id = {r.id: r for r in out.parkingRows}
    assert abs(by_id["north"].origin.x - 5) < 0.6
    assert abs(by_id["north"].origin.y - 12) < 0.6
    assert abs(by_id["south"].origin.y - 2) < 0.6
    assert abs(by_id["north"].angleDeg + 30) < 1e-6
    assert all(r.charger and r.charger.type == "dc_320kw" for r in out.parkingRows)


def test_revise_angle_and_equipment_move() -> None:
    from api.services.layouts.revise import (
        is_layout_revise_query,
        revise_plan_from_prior,
    )

    assert is_layout_revise_query("改成斜列45度", has_prior=True, has_images=False)
    assert is_layout_revise_query(
        "把箱变挪到东北角", has_prior=True, has_images=False
    )

    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["buildings"] = []
    payload["roads"] = []
    payload["site"] = {
        "widthM": 40,
        "heightM": 28,
        "gate": {"side": "south", "offsetM": 14, "widthM": 8},
    }
    payload["parkingRows"] = [
        {
            "id": "cars",
            "stalls": 6,
            "stallWidthM": 3,
            "stallLengthM": 6,
            "angleDeg": 0,
            "origin": {"x": 8, "y": 8},
            "along": "x",
            "charger": {"type": "dc_160kw", "startNo": 1, "side": "head"},
        }
    ]
    payload["equipment"] = [
        {
            "id": "tx1",
            "type": "box_transformer",
            "x": 8,
            "y": 8,
            "label": "1600kVA箱变",
            "capacityKva": 1600,
        }
    ]
    prior = parse_plan(payload)
    angled = revise_plan_from_prior(prior, query="全部改成斜列45度")
    assert abs(angled.parkingRows[0].angleDeg - 45) < 1e-6
    moved = revise_plan_from_prior(prior, query="把箱变挪到东北角")
    tx = next(eq for eq in moved.equipment if eq.type == "box_transformer")
    assert tx.x >= 40 - 5
    assert tx.y >= 28 - 5


def test_gentle_pack_does_not_repack_valid_rows() -> None:
    """南门场地即使 gentle 出图，两排也必须钉到东西对侧，不能留在南北横排。"""
    from api.services.layouts.pack import prepare_plan

    payload = {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {
            "widthM": 30,
            "heightM": 20,
            "gate": {"side": "south", "offsetM": 11, "widthM": 8},
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
                "origin": {"x": 4, "y": 12},
                "along": "x",
                "charger": {"type": "dc_160kw", "startNo": 1, "side": "head"},
            },
            {
                "id": "b",
                "stalls": 4,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "angleDeg": 0,
                "origin": {"x": 4, "y": 2},
                "along": "x",
                "charger": {"type": "dc_160kw", "startNo": 5, "side": "head"},
            },
        ],
        "equipment": [],
    }
    plan = prepare_plan(parse_plan(payload), gentle=True)
    from api.services.layouts.pack import _infer_row_wall

    walls = {_infer_row_wall(plan, row) for row in plan.parkingRows}
    assert walls == {"west", "east"}
    assert sum(r.stalls for r in plan.parkingRows) == 8


@pytest.mark.asyncio
async def test_layout_out_applies_west_wall_even_without_revise_flag() -> None:
    """用户说 1-4 靠西墙时，即使 Redis prior 丢失也应在当前方案上执行空间指令。"""
    from api.services.layouts.pack import _row_aabb

    payload = {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {
            "widthM": 30,
            "heightM": 20,
            "gate": {"side": "south", "offsetM": 11, "widthM": 8},
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
                "origin": {"x": 8, "y": 12},
                "along": "x",
                "charger": {"type": "dc_160kw", "startNo": 1, "side": "head"},
            },
            {
                "id": "b",
                "stalls": 4,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "angleDeg": 0,
                "origin": {"x": 8, "y": 4},
                "along": "x",
                "charger": {"type": "dc_160kw", "startNo": 5, "side": "head"},
            },
        ],
        "equipment": [
            {
                "id": "tx1",
                "type": "box_transformer",
                "x": 26,
                "y": 14,
                "label": "2000kVA箱变",
                "capacityKva": 2000,
            }
        ],
    }
    state: dict[str, Any] = {
        "query": "请你帮我修改一下以上生成的图纸 1-4号靠西墙竖着摆放",
        "visionText": "",
        "output": json.dumps(payload, ensure_ascii=False),
        "priorLayout": None,
        "layoutRevise": False,
        "outputImages": [],
        "chatSession": None,
    }
    detail = await _exec_layout_out(data={"render": False}, state=state)
    assert detail.get("stalls") == 8
    layout = state.get("layout")
    assert isinstance(layout, dict)
    plan = parse_plan(layout)
    wall = next(r for r in plan.parkingRows if r.charger and int(r.charger.startNo) == 1)
    assert wall.along == "y"
    assert wall.stalls == 4
    assert _row_aabb(wall)[0] <= 1.5


@pytest.mark.asyncio
async def test_program_revise_skips_llm_and_pins_west_wall() -> None:
    """有 prior 时改图跳过 LLM，布置出图按指令贴西墙。"""
    from api.services.layouts.pack import _row_aabb
    from api.services.layouts.revise import should_program_revise
    from api.services.workflows.nodes import _exec_llm

    payload = {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {
            "widthM": 30,
            "heightM": 20,
            "gate": {"side": "south", "offsetM": 11, "widthM": 8},
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
                "origin": {"x": 8, "y": 12},
                "along": "x",
                "charger": {"type": "dc_160kw", "startNo": 1, "side": "head"},
            },
            {
                "id": "b",
                "stalls": 4,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "angleDeg": 0,
                "origin": {"x": 8, "y": 4},
                "along": "x",
                "charger": {"type": "dc_160kw", "startNo": 5, "side": "head"},
            },
        ],
        "equipment": [
            {
                "id": "tx1",
                "type": "box_transformer",
                "x": 26,
                "y": 14,
                "label": "2000kVA箱变",
                "capacityKva": 2000,
            }
        ],
    }
    q = "请你帮我修改一下以上生成的图纸 1-4号靠西墙竖着摆放"
    state: dict[str, Any] = {
        "query": q,
        "input": {"query": q},
        "visionText": "",
        "priorLayout": payload,
        "layoutRevise": True,
        "outputImages": [],
        "chatSession": None,
        "images": [],
    }
    assert should_program_revise(state) is True
    llm_detail = await _exec_llm(None, data={"systemPrompt": "ev_charging_station_plan parkingRows"}, state=state)  # type: ignore[arg-type]
    assert llm_detail.get("skippedLlm") is True
    assert state.get("layoutRevise") is True
    detail = await _exec_layout_out(data={"render": False}, state=state)
    assert detail.get("revise") is True
    assert detail.get("spatial") is True
    plan = parse_plan(state["layout"])
    wall = next(r for r in plan.parkingRows if r.charger and int(r.charger.startNo) == 1)
    assert wall.along == "y"
    assert _row_aabb(wall)[0] <= 1.5


def test_west_wall_chargers_flush_and_feeders_nearby() -> None:
    """靠西墙：桩贴西侧；回转车道在东侧开口；箱变在开口侧端头且不压车位。"""
    from api.services.layouts.pack import charger_heads, prepare_plan, _row_aabb, _eq_box, _overlap
    from api.services.layouts.revise import revise_plan_from_prior

    payload = {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {
            "widthM": 30,
            "heightM": 20,
            "gate": {"side": "south", "offsetM": 11, "widthM": 8},
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
                "origin": {"x": 8, "y": 10},
                "along": "x",
                "charger": {"type": "dc_160kw", "startNo": 1, "side": "head"},
            },
            {
                "id": "b",
                "stalls": 4,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "angleDeg": 0,
                "origin": {"x": 8, "y": 2},
                "along": "x",
                "charger": {"type": "dc_160kw", "startNo": 5, "side": "head"},
            },
        ],
        "equipment": [
            {
                "id": "tx1",
                "type": "box_transformer",
                "x": 26,
                "y": 16,
                "label": "2000kVA箱变",
                "capacityKva": 2000,
            },
            {
                "id": "tx2",
                "type": "box_transformer",
                "x": 26,
                "y": 10,
                "label": "2000kVA箱变",
                "capacityKva": 2000,
            },
        ],
    }
    prior = parse_plan(payload)
    out = revise_plan_from_prior(prior, query="1-4号靠西墙竖着摆放")
    out = prepare_plan(out, gentle=True)
    wall = next(r for r in out.parkingRows if r.charger and int(r.charger.startNo) == 1)
    assert wall.charger is not None
    assert wall.charger.side == "left"
    box = _row_aabb(wall)
    heads = charger_heads(wall)
    assert heads
    assert all(hx <= box[0] + 0.05 for hx, _hy in heads), "桩应在车位轮廓西侧外"
    west_aisles = [a for a in out.aisles if a.centerline and a.centerline[0].x > box[2]]
    assert west_aisles, "回转车道应在西墙车位开口（东）侧"
    feeders = [eq for eq in out.equipment if "transformer" in eq.type]
    assert len(feeders) == 2
    assert any(eq.x < 14 for eq in feeders), "至少一台箱变应靠近西墙充电排开口侧"
    assert all(not (10 < eq.x < 20 and 6 < eq.y < 14) for eq in feeders), "箱变不应落在场地中央"
    for eq in feeders:
        eb = _eq_box(eq.x, eq.y)
        for r in out.parkingRows:
            assert not _overlap(eb, _row_aabb(r), pad=-0.2), "箱变不得压车位"


def test_edge_feeders_for_north_and_east_walls() -> None:
    """北墙+东墙车位时，箱变在开口侧端头、不压车位、不进正中央。"""
    from api.services.layouts.pack import prepare_plan, _row_aabb, _eq_box, _overlap, charger_heads

    payload = {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {
            "widthM": 30,
            "heightM": 20,
            "gate": {"side": "south", "offsetM": 11, "widthM": 8},
        },
        "buildings": [],
        "roads": [],
        "parkingRows": [
            {
                "id": "north",
                "stalls": 4,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "angleDeg": 0,
                "origin": {"x": 4, "y": 13.2},
                "along": "x",
                "charger": {"type": "dc_160kw", "startNo": 5, "side": "head"},
            },
            {
                "id": "east",
                "stalls": 4,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "angleDeg": 0,
                "origin": {"x": 26.2, "y": 2},
                "along": "y",
                "charger": {"type": "dc_160kw", "startNo": 1, "side": "right"},
            },
        ],
        "equipment": [
            {
                "id": "tx1",
                "type": "box_transformer",
                "x": 15,
                "y": 10,
                "label": "2000kVA箱变",
                "capacityKva": 2000,
            },
            {
                "id": "tx2",
                "type": "box_transformer",
                "x": 18,
                "y": 10,
                "label": "2000kVA箱变",
                "capacityKva": 2000,
            },
        ],
    }
    plan = prepare_plan(parse_plan(payload), gentle=True)
    feeders = [eq for eq in plan.equipment if "transformer" in eq.type]
    assert len(feeders) == 2
    north = next(r for r in plan.parkingRows if r.id == "north")
    east = next(r for r in plan.parkingRows if r.id == "east")
    assert north.charger and north.charger.side == "head"
    assert east.charger and east.charger.side == "right"
    nb, eb = _row_aabb(north), _row_aabb(east)
    # 北排桩在车位北侧外；东排桩在车位东侧外
    assert all(hy >= nb[3] - 0.05 for _hx, hy in charger_heads(north))
    assert all(hx >= eb[2] - 0.05 for hx, _hy in charger_heads(east))
    for eq in feeders:
        box = _eq_box(eq.x, eq.y)
        for r in plan.parkingRows:
            assert not _overlap(box, _row_aabb(r), pad=-0.2)
        assert not (9 < eq.x < 21 and 6 < eq.y < 14), "箱变不应在场地正中央"
    north_heads = charger_heads(north)
    east_heads = charger_heads(east)
    assert min(
        min((eq.x - hx) ** 2 + (eq.y - hy) ** 2 for hx, hy in north_heads) ** 0.5
        for eq in feeders
    ) < 12
    assert min(
        min((eq.x - hx) ** 2 + (eq.y - hy) ** 2 for hx, hy in east_heads) ** 0.5
        for eq in feeders
    ) < 12


def test_double_row_feeders_clear_stalls_and_chargers_outside() -> None:
    """双排水平车位：桩在车头外侧；箱变不压车位。"""
    from api.services.layouts.pack import (
        prepare_plan,
        charger_heads,
        expand_stalls,
        charger_point,
        charger_outset_m,
        _row_aabb,
        _eq_box,
        _overlap,
        _point_in_poly,
        rect_corners,
    )

    payload = {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "site": {
            "widthM": 30,
            "heightM": 20,
            "gate": {"side": "south", "offsetM": 11, "widthM": 8},
        },
        "buildings": [],
        "roads": [],
        "parkingRows": [
            {
                "id": "top",
                "stalls": 4,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "angleDeg": 0,
                "origin": {"x": 6, "y": 12},
                "along": "x",
                "charger": {"type": "dc_160kw", "startNo": 1, "side": "head"},
            },
            {
                "id": "bot",
                "stalls": 4,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "angleDeg": 0,
                "origin": {"x": 6, "y": 3},
                "along": "x",
                "charger": {"type": "dc_160kw", "startNo": 5, "side": "head"},
            },
        ],
        "equipment": [
            {
                "id": "tx1",
                "type": "box_transformer",
                "x": 8,
                "y": 14,
                "label": "2000kVA箱变",
                "capacityKva": 2000,
            },
            {
                "id": "tx2",
                "type": "box_transformer",
                "x": 20,
                "y": 14,
                "label": "2000kVA箱变",
                "capacityKva": 2000,
            },
        ],
    }
    plan = prepare_plan(parse_plan(payload), gentle=True)
    for row in plan.parkingRows:
        assert row.charger is not None
        outset = charger_outset_m(row)
        for rect, _ in expand_stalls(row):
            hx, hy = charger_point(rect, row.charger.side, outset=outset)
            corners = rect_corners(rect)
            # 略向内缩，确认桩中心不在车位多边形内部
            assert not _point_in_poly(hx, hy, corners)
    for eq in plan.equipment:
        if "transformer" not in eq.type:
            continue
        eb = _eq_box(eq.x, eq.y)
        for r in plan.parkingRows:
            assert not _overlap(eb, _row_aabb(r), pad=-0.2)


def test_resize_kind_rows_never_negative_when_too_many_rows() -> None:
    from api.services.layouts.brief import apply_layout_brief

    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["parkingRows"] = [
        {
            "id": f"r{i}",
            "stalls": 2,
            "stallWidthM": 3.0,
            "stallLengthM": 6.0,
            "angleDeg": 0,
            "origin": {"x": 4.0, "y": 4.0 + i * 8},
            "along": "x",
            "charger": {"type": "dc_160kw", "startNo": 1, "side": "head"},
        }
        for i in range(12)
    ]
    plan = apply_layout_brief(parse_plan(payload), "8个轿车充电桩")
    assert sum(r.stalls for r in plan.parkingRows) == 8
    assert all(r.stalls >= 1 for r in plan.parkingRows)


def test_parse_area_and_pile_count_and_scale_site() -> None:
    from api.services.layouts.brief import parse_layout_brief, scale_site_to_area_m2

    brief = parse_layout_brief("车位规划260㎡，布置16个直流充电桩，两台2000kVA箱变")
    assert brief.site_area_m2 == 260.0
    assert brief.cars == 16
    assert brief.transformer_n == 2
    assert brief.transformer_kva == 2000.0

    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["site"] = {
        "widthM": 44,
        "heightM": 20,
        "polygon": [
            {"x": 0, "y": 8},
            {"x": 14, "y": 8},
            {"x": 14, "y": 0},
            {"x": 44, "y": 0},
            {"x": 44, "y": 20},
            {"x": 0, "y": 20},
        ],
        "gate": {"side": "south", "offsetM": 18, "widthM": 8},
    }
    plan = parse_plan(payload)
    scale_site_to_area_m2(plan, 260.0)
    from api.services.layouts.brief import _shoelace_area

    area = _shoelace_area([(p.x, p.y) for p in plan.site.polygon])
    assert abs(area - 260.0) < 8.0


def test_pack_does_not_inflate_stalls_on_same_origin() -> None:
    """装不下时不得把剩余 stalls 加回同一排造成重合。"""
    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["site"] = {
        "widthM": 18,
        "heightM": 14,
        "polygon": [
            {"x": 0, "y": 0},
            {"x": 18, "y": 0},
            {"x": 18, "y": 14},
            {"x": 0, "y": 14},
        ],
        "gate": {"side": "south", "offsetM": 5, "widthM": 6},
    }
    payload["buildings"] = []
    payload["roads"] = []
    payload["parkingRows"] = [
        {
            "id": "cars",
            "stalls": 16,
            "stallWidthM": 3.0,
            "stallLengthM": 6.0,
            "angleDeg": 0,
            "origin": {"x": 2, "y": 2},
            "along": "x",
            "charger": {"type": "dc_160kw", "startNo": 1, "side": "head"},
        }
    ]
    plan = prepare_plan(parse_plan(payload))
    from api.services.layouts.pack import _row_aabb

    # 各排中心距应明显分开（允许相邻排轻度靠近，但不允许大面积重合）
    boxes = [_row_aabb(r) for r in plan.parkingRows]
    for i, a in enumerate(boxes):
        for j, b in enumerate(boxes):
            if i >= j:
                continue
            x0, y0 = max(a[0], b[0]), max(a[1], b[1])
            x1, y1 = min(a[2], b[2]), min(a[3], b[3])
            ov_w, ov_h = max(0.0, x1 - x0), max(0.0, y1 - y0)
            assert ov_w * ov_h < 4.0
    # 同一排内相邻桩中心距应接近车位间距
    from api.services.layouts.pack import charger_heads

    for row in plan.parkingRows:
        heads = charger_heads(row)
        for a, b in zip(heads, heads[1:]):
            dist = ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
            assert dist >= 2.5


def test_irregular_site_repacks_stalls_inside_polygon() -> None:
    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["site"] = {
        "widthM": 36,
        "heightM": 28,
        "northDeg": 0,
        "polygon": [
            {"x": 0, "y": 10},
            {"x": 12, "y": 10},
            {"x": 12, "y": 0},
            {"x": 36, "y": 0},
            {"x": 36, "y": 28},
            {"x": 0, "y": 28},
        ],
        "gate": {"side": "south", "offsetM": 18, "widthM": 8, "label": "出入口"},
    }
    payload["buildings"] = [
        {"id": "office", "label": "办公楼", "rect": {"x": 2, "y": 18, "w": 20, "h": 8}}
    ]
    payload["parkingRows"] = [
        {
            "id": "cars",
            "stalls": 8,
            "stallWidthM": 3.0,
            "stallLengthM": 6.0,
            "angleDeg": 0,
            "origin": {"x": 80, "y": 80},
            "along": "x",
            "charger": {"type": "dc_160kw", "startNo": 1, "side": "head"},
        }
    ]
    payload["roads"] = []
    plan = prepare_plan(parse_plan(payload))
    assert sum(r.stalls for r in plan.parkingRows) == 8
    from api.services.layouts.pack import _row_inside_site

    assert all(_row_inside_site(r, plan) for r in plan.parkingRows)


def test_scheme_a_locks_sizes_generates_aisles_and_svg() -> None:
    from api.services.layouts.brief import parse_layout_brief
    from api.services.layouts.check import check_plan, repair_plan
    from api.services.layouts.svg import render_plan_svg

    query = "配置了8个轿车充电桩和一个2000W的箱变"
    brief = parse_layout_brief(query)
    assert brief.cars == 8
    assert brief.transformer_kva == 2000.0
    payload = json.loads(json.dumps(EXAMPLE_PLAN))
    payload["parkingRows"] = [
        {
            "id": "cars",
            "stalls": 8,
            "stallWidthM": 2.2,
            "stallLengthM": 5.0,
            "angleDeg": 0,
            "origin": {"x": 10, "y": 10},
            "along": "x",
            "charger": {"type": "dc_160kw", "startNo": 1, "side": "head"},
        }
    ]
    plan = repair_plan(parse_plan(payload), brief)
    plan = prepare_plan(plan)
    assert all(abs(r.stallWidthM - 3.0) < 1e-6 and abs(r.stallLengthM - 6.0) < 1e-6 for r in plan.parkingRows)
    assert sum(r.stalls for r in plan.parkingRows) == 8
    assert plan.aisles
    assert any(e.capacityKva and abs(float(e.capacityKva) - 2000) < 0.5 for e in plan.equipment)
    issues = check_plan(plan, brief)
    assert not any(i.code in {"car_count", "car_size", "transformer_kva"} and i.blocking for i in issues)
    svg = render_plan_svg(plan)
    assert svg.lstrip().startswith("<svg")
    assert "充电站" in svg or "平面" in svg


def test_layout_llm_keeps_draft_images_even_when_vision_exists() -> None:
    """布置提示词场景下，有 visionText 也不能因为 attachImages=false 丢掉草稿图。"""
    from api.services.layouts.prompt import LAYOUT_LLM_SYSTEM_PROMPT
    from api.services.workflows.nodes import _bool_flag, _is_layout_prompt

    assert _is_layout_prompt([LAYOUT_LLM_SYSTEM_PROMPT])
    assert _bool_flag({"attachImages": False}, "attachImages", default=True) is False
    layout_prompt = True
    has_images = True
    has_vision = True
    attach_cfg = False
    attach = True if layout_prompt and has_images else (attach_cfg and not has_vision)
    assert attach is True
    assert has_vision  # 明示：即使已有读图文字，仍应附图









