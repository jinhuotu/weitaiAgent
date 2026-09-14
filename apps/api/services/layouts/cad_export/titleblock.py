"""可选公司图框：从 DXF 模板插入图纸空间块并填属性。没有模板则返回 False。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from api.services.layouts.cad_export.layers import LAYER_SHEET
from api.services.layouts.cad_export.units import PAPER_MM
from api.services.layouts.schema import EvChargingStationPlan
from common.config import get_settings

_BLOCK_CANDIDATES = ("WT_TITLE", "TITLE", "TITLE_A3", "TITLE_A2", "TITLE_A1", "图框")
_ATTR_MAP = (
    ("PROJECT", "project"),
    ("工程名称", "project"),
    ("TITLE", "title"),
    ("图名", "title"),
    ("SHEET", "sheetNo"),
    ("SHEETNO", "sheetNo"),
    ("图号", "sheetNo"),
    ("DATE", "date"),
    ("日期", "date"),
    ("DESIGN", "designer"),
    ("设计", "designer"),
    ("REVIEW", "reviewer"),
    ("审核", "reviewer"),
    ("DRAW", "drawer"),
    ("DRAWER", "drawer"),
    ("制图", "drawer"),
    ("COMPANY", "company"),
    ("单位", "company"),
    ("STAGE", "stage"),
    ("阶段", "stage"),
)


def resolve_title_block_path(paper: str) -> Path | None:
    settings = get_settings()
    configured = (getattr(settings, "cad_title_block_dxf", "") or "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    root = _repo_root()
    key = (paper or "A3").upper()
    candidates.extend(
        [
            root / "assets" / "cad" / f"title_{key.lower()}.dxf",
            root / "assets" / "cad" / "title.dxf",
        ]
    )
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved.is_file() and resolved.suffix.lower() == ".dxf":
            return resolved
    return None


def insert_title_block(
    doc: Any,
    psp: Any,
    plan: EvChargingStationPlan,
    *,
    paper: str,
    scale_n: int,
) -> bool:
    path = resolve_title_block_path(paper)
    if path is None:
        return False
    try:
        return _insert(doc, psp, plan, path, paper=paper, scale_n=scale_n)
    except Exception:  # noqa: BLE001
        return False


def _insert(
    doc: Any,
    psp: Any,
    plan: EvChargingStationPlan,
    path: Path,
    *,
    paper: str,
    scale_n: int,
) -> bool:
    import ezdxf
    from ezdxf.addons.importer import Importer

    src = ezdxf.readfile(str(path))
    name = _pick_block_name(src)
    importer = Importer(src, doc)
    if name:
        importer.import_block(name)
        importer.finalize()
        block_name = name
    else:
        block_name = "WT_TITLE"
        if block_name not in doc.blocks:
            blk = doc.blocks.new(name=block_name)
            importer.import_entities(list(src.modelspace()), blk)
            importer.finalize()
        else:
            importer.finalize()
    if block_name not in doc.blocks:
        return False
    pw, ph = PAPER_MM.get(paper, PAPER_MM["A3"])
    insert = _insert_point(doc.blocks.get(block_name), pw, ph)
    ref = psp.add_blockref(
        block_name,
        insert=insert,
        dxfattribs={"layer": LAYER_SHEET},
    )
    values = _attr_values(plan, scale_n)
    try:
        if hasattr(ref, "add_auto_attribs"):
            ref.add_auto_attribs(values)
        elif hasattr(ref, "attribs"):
            for attrib in ref.attribs:
                tag = str(attrib.dxf.tag or "").upper()
                if tag in values:
                    attrib.dxf.text = values[tag]
    except Exception:  # noqa: BLE001
        pass
    return True


def _pick_block_name(src: Any) -> str | None:
    names = [
        block.dxf.name
        for block in src.blocks
        if not str(block.dxf.name).startswith("*")
    ]
    for cand in _BLOCK_CANDIDATES:
        if cand in names:
            return cand
    return names[0] if names else None


def _insert_point(block: Any, pw: float, ph: float) -> tuple[float, float]:
    xs: list[float] = []
    ys: list[float] = []
    try:
        for entity in block:
            kind = entity.dxftype()
            if kind == "LWPOLYLINE":
                for pt in entity:
                    xs.append(float(pt[0]))
                    ys.append(float(pt[1]))
            elif kind == "LINE":
                xs.extend([float(entity.dxf.start.x), float(entity.dxf.end.x)])
                ys.extend([float(entity.dxf.start.y), float(entity.dxf.end.y)])
    except Exception:  # noqa: BLE001
        return (0.0, 0.0)
    if not xs or not ys:
        return (0.0, 0.0)
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    if width >= pw * 0.55 and height >= ph * 0.55:
        return (0.0, 0.0)
    return (max(12.0, pw - 14.0 - width - min(xs)), max(12.0, 14.0 - min(ys)))


def _attr_values(plan: EvChargingStationPlan, scale_n: int) -> dict[str, str]:
    tb = plan.titleBlock
    fields = {
        "project": (tb.project or tb.title or "充电站").strip(),
        "title": (tb.title or "充电站平面布置图").strip(),
        "sheetNo": (tb.sheetNo or "001").strip(),
        "date": (tb.date or "").strip(),
        "designer": (tb.designer or "").strip(),
        "reviewer": (tb.reviewer or "").strip(),
        "drawer": (tb.drawer or "").strip(),
        "company": (tb.company or "").strip(),
        "stage": (tb.stage or "施工图").strip(),
        "scale": f"1:{scale_n}",
    }
    out: dict[str, str] = {"SCALE": fields["scale"], "比例": fields["scale"]}
    for tag, key in _ATTR_MAP:
        out[tag.upper()] = fields[key]
        out[tag] = fields[key]
    return out


def _repo_root() -> Path:
    configured = (get_settings().weitai_root or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[5]
