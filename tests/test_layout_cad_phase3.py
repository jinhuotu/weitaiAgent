"""第 3 期：测量坐标、补符号、可选 DWG、DXF 回读 JSON。"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_layout_cad_golden import _layout, golden_plan

from api.routers.layouts import _FILE_RE, _download_as
from api.services.layouts.cad_export.dwg import write_plan_dwg
from api.services.layouts.cad_export.layers import (
    BLOCK_HYDRANT,
    BLOCK_TRANSFORMER,
    BLOCK_TREE,
    BLOCK_VENT,
    BLOCK_WELL,
    LAYER_GREEN,
    LAYOUT_NAME,
)
from api.services.layouts.cad_export.reader import plan_from_dxf
from api.services.layouts.cad_export.units import local_to_survey_m, mm
from api.services.layouts.cad_export.writer import plan_to_dxf_bytes, plan_to_dxf_doc
from api.services.layouts.schema import EquipmentSpec, SurveyFrame, TreeSpec


def test_survey_moves_modelspace_to_survey_mm() -> None:
    plan = golden_plan()
    plan.site.survey = SurveyFrame(
        originXm=385000.0, originYm=2480000.0, rotationDeg=0.0, name="工地独立坐标"
    )
    doc = plan_to_dxf_doc(plan)
    txs = [e for e in doc.modelspace().query("INSERT") if e.dxf.name == BLOCK_TRANSFORMER]
    assert len(txs) == 2
    xs = sorted(float(e.dxf.insert.x) for e in txs)
    assert xs[0] == pytest.approx(mm(385000.0 + 24.5), abs=2.0)
    texts = "\n".join(str(e.dxf.text or "") for e in doc.modelspace().query("TEXT"))
    assert "工地独立坐标" in texts
    assert any("本图采用工地独立坐标" in n for n in plan.notes)


def test_dxf_roundtrip_keeps_golden_geometry(tmp_path: Path) -> None:
    import ezdxf

    plan = golden_plan()
    dest = tmp_path / "golden.dxf"
    dest.write_bytes(plan_to_dxf_bytes(plan))
    back = plan_from_dxf(dest)
    assert sum(r.stalls for r in back.parkingRows) == 8
    txs = [eq for eq in back.equipment if eq.type == "box_transformer"]
    assert len(txs) == 2
    assert txs[0].x == pytest.approx(24.5, abs=0.05)
    assert txs[1].x == pytest.approx(31.5, abs=0.05)
    assert txs[0].capacityKva == 2000
    assert back.site.polygon
    assert back.titleBlock.title == "充电站平面布置图"
    from api.services.layouts.cad_export.meta import extract_plan_json

    dumped = extract_plan_json(ezdxf.readfile(str(dest)))
    assert dumped and dumped.get("kind") == "ev_charging_station_plan"


def test_survey_roundtrip_returns_local_meters(tmp_path: Path) -> None:
    plan = golden_plan()
    plan.site.survey = SurveyFrame(originXm=500000.0, originYm=4_000_000.0, rotationDeg=12.0)
    dest = tmp_path / "survey.dxf"
    dest.write_bytes(plan_to_dxf_bytes(plan))
    back = plan_from_dxf(dest)
    txs = [eq for eq in back.equipment if eq.type == "box_transformer"]
    assert txs[0].x == pytest.approx(24.5, abs=0.08)
    assert txs[0].y == pytest.approx(46.4, abs=0.08)
    assert back.site.survey is not None
    assert back.site.survey.originXm == pytest.approx(500000.0)


def test_new_symbol_blocks_and_greenery_hatch() -> None:
    plan = golden_plan()
    plan.equipment.append(EquipmentSpec(id="xh1", type="fire_hydrant", x=8.0, y=8.0))
    plan.equipment.append(EquipmentSpec(id="well1", type="cable_well", x=40.0, y=10.0))
    plan.equipment.append(EquipmentSpec(id="vent1", type="vent_grille", x=20.0, y=6.0))
    plan.trees.append(TreeSpec(x=15.0, y=12.0))
    doc = plan_to_dxf_doc(plan)
    names = [e.dxf.name for e in doc.modelspace().query("INSERT")]
    assert BLOCK_HYDRANT in names
    assert BLOCK_WELL in names
    assert BLOCK_VENT in names
    assert BLOCK_TREE in names
    hatches = [
        e for e in doc.modelspace() if e.dxftype() == "HATCH" and e.dxf.layer == LAYER_GREEN
    ]
    assert hatches
    assert "fire_hydrant" in plan.legend
    psp = _layout(doc, LAYOUT_NAME)
    texts = "\n".join(str(e.dxf.text or "") for e in psp.query("TEXT"))
    assert "消火栓" in texts


def test_dwg_skipped_without_converter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from api.services.layouts.cad_export import dwg as dwg_mod

    monkeypatch.setattr(dwg_mod, "resolve_oda_converter", lambda: None)
    dest = tmp_path / "a.dxf"
    dest.write_bytes(plan_to_dxf_bytes(golden_plan()))
    assert write_plan_dwg(dest) is None


def test_dwg_converter_hook_copies_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from api.services.layouts.cad_export import dwg as dwg_mod

    dest = tmp_path / "station.dxf"
    dest.write_bytes(plan_to_dxf_bytes(golden_plan()))
    fake = tmp_path / "ODAFileConverter.exe"
    fake.write_text("fake", encoding="utf-8")

    def _run(args, **_kwargs):
        Path(args[2]).joinpath("station.dwg").write_bytes(b"dwg-bytes")

        class _Proc:
            returncode = 0

        return _Proc()

    monkeypatch.setattr(dwg_mod, "resolve_oda_converter", lambda: fake)
    monkeypatch.setattr(dwg_mod.subprocess, "run", _run)
    dwg = write_plan_dwg(dest)
    assert dwg is not None
    assert dwg.read_bytes() == b"dwg-bytes"
    stored = "充电站-充电站平面布置图-01-abcd1234.dwg"
    assert _FILE_RE.match(stored)
    assert _download_as(stored) == "充电站-充电站平面布置图-01.dwg"


def test_survey_helpers_rotate() -> None:
    frame = SurveyFrame(originXm=100.0, originYm=200.0, rotationDeg=90.0)
    sx, sy = local_to_survey_m(frame, 3.0, 4.0)
    assert sx == pytest.approx(100.0 - 4.0)
    assert sy == pytest.approx(200.0 + 3.0)
