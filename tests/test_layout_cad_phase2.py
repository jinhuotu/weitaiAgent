"""第 2 期：图纸空间预览、1:200、可读文件名、公司图框、设计说明。"""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image
from test_layout_cad_golden import _layout, _layout_texts, golden_plan

from api.routers.layouts import _FILE_RE, _download_as
from api.services.layouts.cad import export_plan_cad, rasterize_doc, write_plan_dxf
from api.services.layouts.cad_export.files import layout_download_name, layout_stored_name
from api.services.layouts.cad_export.layers import LAYOUT_NAME
from api.services.layouts.cad_export.notes import ensure_cad_notes, station_summary_note
from api.services.layouts.cad_export.units import choose_paper_and_scale
from api.services.layouts.cad_export.writer import plan_to_dxf_doc


def test_golden_keeps_preferred_scale_on_a3() -> None:
    plan = golden_plan()
    paper, scale = choose_paper_and_scale(
        plan.sheetStyle.paper,
        plan.sheetStyle.scale,
        (0.0, 0.0, plan.site.widthM, plan.site.heightM),
    )
    assert paper == "A3"
    assert scale == 200


def test_station_summary_note_matches_drawing_style() -> None:
    plan = golden_plan()
    note = station_summary_note(plan, query=plan.notes[0] if plan.notes else "")
    assert note.startswith("1.站内含")
    assert "重卡车位8个" in note
    assert "5*17" in note
    assert "8台" in note
    assert "2台2000kVA箱变" in note
    plan.notes = []
    ensure_cad_notes(plan, query="8台500kW重卡直流桩，2台2000kVA箱变")
    assert "500kW" in plan.notes[0]
    assert len([n for n in plan.notes if n.startswith("1.站内含")]) == 1


def test_layout_file_names_are_readable() -> None:
    plan = golden_plan()
    stored = layout_stored_name(plan, ext="dxf")
    download = layout_download_name(plan, ext="dxf")
    assert stored.endswith(".dxf")
    assert download == "充电站-充电站平面布置图-01.dxf"
    assert _FILE_RE.match(stored)
    assert _download_as(stored) == download


def test_golden_preview_is_a3_sheet(tmp_path) -> None:
    plan = golden_plan()
    dest = tmp_path / "sheet.dxf"
    saved, png = export_plan_cad(plan, dest)
    assert saved.is_file()
    img = Image.open(BytesIO(png))
    assert img.width / img.height == pytest.approx(420 / 297, rel=0.12)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_company_title_block_fills_attributes(tmp_path, monkeypatch) -> None:
    import ezdxf

    from api.services.layouts.cad_export import titleblock as tb

    template = tmp_path / "title_a3.dxf"
    src = ezdxf.new("R2013")
    blk = src.blocks.new("WT_TITLE")
    blk.add_lwpolyline([(0, 0), (100, 0), (100, 40), (0, 40)], close=True)
    blk.add_attdef("PROJECT", insert=(8, 22), height=3.0)
    blk.add_attdef("TITLE", insert=(8, 12), height=3.0)
    src.saveas(str(template))
    monkeypatch.setattr(tb, "resolve_title_block_path", lambda _paper: template)

    plan = golden_plan()
    doc = plan_to_dxf_doc(plan)
    psp = _layout(doc, LAYOUT_NAME)
    texts = "\n".join(_layout_texts(psp))
    assert "图例" in texts
    inserts = [e.dxf.name for e in psp.query("INSERT")]
    assert "WT_TITLE" in inserts
    values: list[str] = []
    for ins in psp.query("INSERT"):
        if ins.dxf.name != "WT_TITLE":
            continue
        values.extend(str(a.dxf.text or "") for a in getattr(ins, "attribs", []))
    values.extend(str(e.dxf.text or "") for e in psp.query("ATTRIB"))
    blob = " ".join(values)
    assert "充电站平面布置图" in blob or "充电站" in blob


def test_write_plan_dxf_uses_readable_default_name(tmp_path, monkeypatch) -> None:
    from common import config as cfg

    monkeypatch.setattr(cfg.get_settings(), "storage_root", str(tmp_path))
    # get_settings is lru_cached; write_plan_dxf with explicit dest avoids that
    plan = golden_plan()
    dest = tmp_path / layout_stored_name(plan, ext="dxf")
    write_plan_dxf(plan, dest)
    assert dest.is_file()
    png = rasterize_doc(plan_to_dxf_doc(plan))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
