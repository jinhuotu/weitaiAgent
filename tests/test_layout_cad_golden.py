"""第 0 期：CAD 对照样本必须能解析，并锁住图二上的关键数量。

后续 CAD 导出测试请用 golden_plan() 的冻结坐标，不要先跑 prepare_plan：
装箱会挪东排和箱变，对照样本就对不上施工图了。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

from api.services.layouts.cad import rasterize_dxf, write_plan_dxf
from api.services.layouts.cad_export import plan_to_dxf_doc
from api.services.layouts.cad_export.layers import (
    BLOCK_DC_PILE,
    BLOCK_NORTH,
    BLOCK_TRANSFORMER,
    BLOCK_TRUCK,
    DIMSTYLE,
    LAYER_DIM,
    LAYER_EQUIP,
    LAYER_PARK,
    LAYER_VEHICLE,
    LAYOUT_NAME,
)
from api.services.layouts.pack import (
    _is_truck_row,
    _point_in_poly,
    _row_inside_site,
    expand_stalls,
    rect_corners,
)
from api.services.layouts.parse import parse_plan
from api.services.layouts.render import render_plan_png
from api.services.layouts.schema import (
    EvChargingStationPlan,
    ParkingRowSpec,
    site_boundary_m,
    site_is_irregular,
)
from api.services.layouts.v2.constraints import LayoutConstraints, check_constraints

GOLDEN_PATH = Path(__file__).resolve().parent / "fixtures" / "layout_cad_golden.json"


@lru_cache(maxsize=1)
def load_layout_cad_golden() -> dict[str, Any]:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def golden_plan() -> EvChargingStationPlan:
    return parse_plan(load_layout_cad_golden()["plan"])


def golden_constraints() -> LayoutConstraints:
    return LayoutConstraints.model_validate(load_layout_cad_golden()["constraints"])


def test_golden_fixture_exists() -> None:
    assert GOLDEN_PATH.is_file()
    blob = load_layout_cad_golden()
    assert blob["id"] == "layout_cad_golden"
    assert blob["phase"] == 0
    assert "plan" in blob and "constraints" in blob and "cad" in blob
    assert blob["sourceDrawing"]["roadLabel"] == "国道323线"
    assert blob["sourceDrawing"]["dimensionsMm"]["northEdge"] == 72946.82


def test_golden_plan_matches_construction_sample() -> None:
    data = load_layout_cad_golden()
    expect = data["expect"]
    plan = golden_plan()

    assert plan.titleBlock.title == expect["sheetTitle"]
    assert plan.titleBlock.sheetNo == expect["sheetNo"]
    assert plan.sheetStyle.paper == expect["paper"]
    assert plan.sheetStyle.scale == expect["scale"]

    assert site_is_irregular(plan.site) is True
    north = [p for p in site_boundary_m(plan.site) if abs(p[1] - plan.site.heightM) < 1e-6]
    assert max(p[0] for p in north) - min(p[0] for p in north) == pytest.approx(
        expect["northEdgeM"]
    )

    stalls = sum(r.stalls for r in plan.parkingRows)
    trucks = sum(r.stalls for r in plan.parkingRows if r.stallLengthM >= 10)
    cars = stalls - trucks
    assert stalls == expect["stalls"]
    assert trucks == expect["trucks"]
    assert cars == expect["cars"]

    assert [r.id for r in plan.parkingRows] == expect["rowIds"]
    for row, angle, origin in zip(
        plan.parkingRows, expect["anglesDeg"], expect["rowOrigins"], strict=True
    ):
        assert row.stalls == 4
        assert [row.stallWidthM, row.stallLengthM] == expect["catalogStallM"]
        assert row.angleDeg == angle
        assert [row.origin.x, row.origin.y] == origin
        assert _is_truck_row(row)
        assert row.charger is not None
        assert row.charger.type == expect["chargerType"]
        assert len(expand_stalls(row)) == 4
        assert _row_inside_site(row, plan), _outside_hint(row, plan)

    west, east = plan.parkingRows
    assert west.charger is not None and east.charger is not None
    assert west.charger.startNo == 1
    assert east.charger.startNo == 5
    assert west.angleDeg < 0 < east.angleDeg

    txs = [eq for eq in plan.equipment if eq.type == "box_transformer"]
    assert len(txs) == expect["transformers"]
    assert all(eq.capacityKva == expect["transformerKva"] for eq in txs)
    assert [[eq.x, eq.y] for eq in txs] == expect["transformerXY"]

    assert plan.site.gate is not None
    assert plan.site.gate.side == expect["gateSide"]
    assert plan.site.gate.label == expect["gateLabel"]
    assert plan.site.gate.roadLabel == expect["roadLabel"]
    assert any(r.label == expect["roadLabel"] for r in plan.roads)
    assert any("8台500kW" in n and "2000kVA" in n for n in plan.notes)


def test_golden_constraints_lock_counts() -> None:
    cons = golden_constraints()
    plan = golden_plan()
    assert cons.fleet.trucks == 8
    assert cons.fleet.cars == 0
    assert cons.fleet.piles == 8
    assert cons.layout.mode == "dual_row_angle"
    assert cons.layout.wallSide == "north"
    assert cons.transformers[0].count == 2
    assert cons.transformers[0].kva == 2000
    issues = check_constraints(plan, cons)
    blocking = [i for i in issues if i.blocking]
    assert blocking == [], [f"{i.code}:{i.message}" for i in blocking]


def test_golden_cad_contract_is_complete() -> None:
    cad = load_layout_cad_golden()["cad"]
    assert cad["units"] == "mm"
    assert cad["insunits"] == 4
    assert cad["paper"] == "A3"
    assert cad["scale"] == "1:200"
    layers = [row["name"] for row in cad["layers"]]
    for name in (
        "WT-SITE",
        "WT-PARK",
        "WT-VEHICLE",
        "WT-EQUIP",
        "WT-ROAD",
        "WT-DIM",
        "WT-ANNO",
        "WT-GREEN",
        "WT-SHEET",
    ):
        assert name in layers
    for block in ("WT_TRUCK", "WT_DC_PILE", "WT_TRANSFORMER", "WT_NORTH"):
        assert block in cad["blocks"]
    for item in ("title_block", "legend", "north_arrow", "notes", "green_dimensions"):
        assert item in cad["mustHave"]


def test_golden_plan_renders_png_and_dxf(tmp_path) -> None:
    plan = golden_plan()
    png = render_plan_png(plan)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    dest = tmp_path / "golden.dxf"
    write_plan_dxf(plan, dest)
    raw = dest.read_bytes()
    assert b"SECTION" in raw
    preview = rasterize_dxf(dest)
    assert preview[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(preview) > 2000


@pytest.fixture(scope="module")
def golden_dxf():
    return plan_to_dxf_doc(golden_plan())


def test_golden_dxf_is_millimeter_cad_base(golden_dxf) -> None:
    cad = load_layout_cad_golden()["cad"]
    assert golden_dxf.header["$INSUNITS"] == cad["insunits"]
    layers = {layer.dxf.name for layer in golden_dxf.layers}
    for row in cad["layers"]:
        assert row["name"] in layers
    blocks = {block.dxf.name for block in golden_dxf.blocks}
    for name in cad["blocks"]:
        assert name in blocks
    auditor = golden_dxf.audit()
    assert auditor.errors == []


def test_golden_dxf_uses_blocks_and_green_dimensions(golden_dxf) -> None:
    msp = golden_dxf.modelspace()
    inserts = [e.dxf.name for e in msp.query("INSERT")]
    assert inserts.count(BLOCK_TRUCK) == 8
    assert inserts.count(BLOCK_DC_PILE) == 8
    assert inserts.count(BLOCK_TRANSFORMER) == 2
    assert all(
        e.dxf.layer == LAYER_VEHICLE
        for e in msp.query("INSERT")
        if e.dxf.name == BLOCK_TRUCK
    )
    assert all(
        e.dxf.layer == LAYER_EQUIP
        for e in msp.query("INSERT")
        if e.dxf.name in {BLOCK_DC_PILE, BLOCK_TRANSFORMER}
    )
    park = [e for e in msp.query("LWPOLYLINE") if e.dxf.layer == LAYER_PARK]
    assert len(park) >= 8
    dims = list(msp.query("DIMENSION"))
    assert len(dims) >= 6
    assert all(e.dxf.layer == LAYER_DIM for e in dims)
    tx = golden_dxf.blocks.get(BLOCK_TRANSFORMER)
    assert sum(1 for e in tx if e.dxftype() == "CIRCLE") == 2
    labels = [e.dxf.text for e in msp.query("TEXT")]
    assert any("出入口" in t for t in labels)
    assert any("国道323线" in t for t in labels)


def test_golden_dxf_paperspace_has_sheet_chrome(golden_dxf) -> None:
    psp = _layout(golden_dxf, LAYOUT_NAME)
    texts = _layout_texts(psp)
    blob = "\n".join(texts)
    assert "图例" in blob
    assert "充电站平面布置图" in blob
    assert "设计说明" in blob
    assert "北" in blob
    assert "施工图" in blob
    assert "1:200" in texts
    assert any(e.dxf.name == BLOCK_NORTH for e in psp.query("INSERT"))
    assert any(e.dxftype() == "VIEWPORT" and int(e.dxf.status) >= 2 for e in psp)
    assert golden_dxf.header["$DIMSTYLE"] == DIMSTYLE


def _layout(doc, name: str):
    for layout in doc.layouts:
        if layout.name == name:
            return layout
    names = [layout.name for layout in doc.layouts]
    raise AssertionError(f"missing layout {name!r}, have {names}")


def _layout_texts(layout) -> list[str]:
    out: list[str] = []
    for entity in layout:
        kind = entity.dxftype()
        if kind == "TEXT":
            out.append(str(entity.dxf.text or ""))
        elif kind == "MTEXT":
            out.append(entity.plain_text())
    return out


def _outside_hint(row: ParkingRowSpec, plan: EvChargingStationPlan) -> str:
    poly = site_boundary_m(plan.site)
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    bad: list[str] = []
    for rect, i in expand_stalls(row):
        for x, y in rect_corners(rect):
            reasons: list[str] = []
            if x < -0.05 or y < -0.05 or x > w + 0.05 or y > h + 0.05:
                reasons.append("bbox")
            if poly and not _point_in_poly(x, y, poly):
                reasons.append("polygon")
            if reasons:
                bad.append(f"stall{i}=({x:.2f},{y:.2f}) {'+'.join(reasons)}")
    return f"{row.id} outside: {bad}"
