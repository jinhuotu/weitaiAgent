"""投标 Word 中缺失扫描件的小标题 + 方框占位。程序排版，不经 LLM。"""

from __future__ import annotations

import io
import logging
from pathlib import Path

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from docx.table import Table, _Cell

from api.services.tenders.schema import PlaceholderItem

logger = logging.getLogger("api.tenders")

_SONG = "宋体"
_CN_ORD = "一二三四五六七八九十"
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

DEFAULT_SLOTS: tuple[PlaceholderItem, ...] = (
    PlaceholderItem(
        key="id_legal",
        title="法定代表人身份证正反面",
        hint="须为河南伟泰光电科技有限公司法定代表人证件扫描件。",
    ),
    PlaceholderItem(
        key="id_agent",
        title="授权代理人身份证及近半年社保缴纳证明",
        hint="法人亲自投标可划掉本项。须为本人证件。",
    ),
    PlaceholderItem(
        key="perf",
        title="类似项目合同及发票（单份金额≥20万元）",
        hint="签约主体必须是河南伟泰光电科技有限公司。",
    ),
    PlaceholderItem(
        key="finance",
        title="近三年财务审计报告 / 完税证明 / 社保缴纳证明",
        hint="按招标文件资格审查条款提供。",
    ),
    PlaceholderItem(
        key="credit",
        title="信用中国查询页及国家企业信用信息公示（股东）",
        hint="截图须含查询日期。",
    ),
    PlaceholderItem(
        key="product",
        title="所投产品检测报告 / 3C / 对应功率桩型证明",
        hint="功率与招标要求一致；正偏离须另附厂家盖章说明。",
    ),
    PlaceholderItem(
        key="bond",
        title="投标保证金缴存回单",
        hint="按邀请书载明的金额与账户缴纳后替换。",
    ),
    PlaceholderItem(
        key="seal",
        title="投标文件签章页",
        hint="逐页盖投标人公章；法定代表人签字或盖章。",
    ),
)

_PERF_NOTE = (
    "【待补】请粘贴河南伟泰光电科技有限公司作为签约主体、金额≥20万元的合同及发票。"
    "禁止使用其他公司业绩。"
)


def collect_slots(extra: list[PlaceholderItem] | None = None) -> list[PlaceholderItem]:
    """默认方框 + 邀请书多出来的资料项（按 key 去重，后者可改说明）。"""
    by_key: dict[str, PlaceholderItem] = {item.key: item.model_copy() for item in DEFAULT_SLOTS if item.key}
    for item in extra or []:
        title = (item.title or "").strip()
        if not title:
            continue
        key = (item.key or "").strip() or f"extra_{len(by_key) + 1}"
        hint = (item.hint or "").strip()
        if key in by_key:
            current = by_key[key]
            by_key[key] = PlaceholderItem(
                key=key,
                title=current.title,
                hint=hint or current.hint,
            )
        else:
            by_key[key] = PlaceholderItem(key=key, title=title, hint=hint or "请按招标文件要求补附")
    return list(by_key.values())


def append_placeholder_section(
    doc: Document,
    slots: list[PlaceholderItem],
    attachments: dict[str, list[Path]] | None = None,
) -> tuple[int, int]:
    """每个附件项：小标题 +（已上传扫描件 | 虚线粘贴框）。返回 (已插入项数, 仍待补方框数)。"""
    if not slots:
        return 0, 0
    files_by_key = attachments or {}
    doc.add_page_break()
    filled = 0
    boxes = 0
    for i, slot in enumerate(slots):
        if i:
            doc.add_paragraph()
        _write_subheading(doc, i, slot.title)
        media = files_by_key.get((slot.key or "").strip()) or []
        if media:
            n = _insert_slot_media(doc, media)
            if n > 0:
                filled += 1
            else:
                _draw_box(doc, slot)
                boxes += 1
        else:
            _draw_box(doc, slot)
            boxes += 1
    return filled, boxes


def fill_perf_placeholders(table: Table) -> None:
    """企业业绩表空单元格写入待补说明，避免空白被误认为已填。"""
    if not table.rows:
        return
    start = 1 if len(table.rows) > 1 else 0
    for row in table.rows[start:]:
        for cell in row.cells:
            compact = "".join((cell.text or "").split())
            if compact and compact not in {"／", "/", "—", "-", "……", "..."}:
                continue
            _write_plain(cell, _PERF_NOTE)


def _ordinal(index: int) -> str:
    if 0 <= index < len(_CN_ORD):
        return _CN_ORD[index]
    return str(index + 1)


def _write_subheading(doc: Document, index: int, title: str) -> None:
    para = doc.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    para.paragraph_format.space_before = Pt(6)
    para.paragraph_format.space_after = Pt(6)
    run = para.add_run(f"（{_ordinal(index)}）{title}")
    _font(run, 14, bold=True)


def _draw_box(doc: Document, slot: PlaceholderItem) -> None:
    table = doc.add_table(rows=1, cols=1)
    _set_dashed_borders(table)
    table.autofit = False
    cell = table.rows[0].cells[0]
    cell.width = Cm(16.0)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    _set_row_height(table.rows[0], 4.8)
    _set_cell_margins(cell, top=160, bottom=160, left=180, right=180)
    cell.text = ""
    mark = cell.paragraphs[0]
    mark.alignment = WD_ALIGN_PARAGRAPH.CENTER
    m_run = mark.add_run("（在此粘贴扫描件）")
    _font(m_run, 10.5, bold=False)
    m_run.font.color.rgb = RGBColor(0x7A, 0x7A, 0x7A)
    _ = slot


def _insert_slot_media(doc: Document, files: list[Path], *, max_pages: int = 20) -> int:
    inserted = 0
    for path in files:
        suf = path.suffix.lower()
        try:
            if suf == ".pdf":
                inserted += _insert_pdf_pages(doc, path, max_pages=max_pages)
            elif suf in _IMAGE_EXT:
                if _insert_image(doc, path):
                    inserted += 1
        except Exception:
            logger.exception("insert slot media failed: %s", path)
    return inserted


def _insert_image(doc: Document, path: Path, *, width_cm: float = 15.5) -> bool:
    if not path.is_file():
        return False
    pic = doc.add_paragraph()
    pic.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pic.paragraph_format.space_before = Pt(4)
    pic.paragraph_format.space_after = Pt(4)
    pic.add_run().add_picture(str(path), width=Cm(width_cm))
    return True


def _insert_pdf_pages(doc: Document, pdf_path: Path, *, max_pages: int = 20) -> int:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        logger.warning("pypdfium2 missing, skip slot pdf %s", pdf_path.name)
        return 0
    try:
        pdf = pdfium.PdfDocument(str(pdf_path))
    except Exception:
        logger.exception("open slot pdf failed: %s", pdf_path)
        return 0

    count = min(len(pdf), max_pages)
    inserted = 0
    for i in range(count):
        try:
            page = pdf[i]
            bitmap = page.render(scale=1.2)
            image = bitmap.to_pil().convert("RGB")
            buf = io.BytesIO()
            image.save(buf, format="JPEG", quality=72, optimize=True)
            buf.seek(0)
            pic = doc.add_paragraph()
            pic.alignment = WD_ALIGN_PARAGRAPH.CENTER
            pic.paragraph_format.space_before = Pt(4)
            pic.paragraph_format.space_after = Pt(4)
            pic.add_run().add_picture(buf, width=Cm(15.5))
            inserted += 1
        except Exception:
            logger.exception("render slot pdf page %s of %s failed", i + 1, pdf_path.name)
    return inserted


def _write_plain(cell: _Cell, text: str) -> None:
    cell.text = ""
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    para = cell.paragraphs[0]
    para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = para.add_run(text)
    _font(run, 9, bold=False)


def _font(run, size_pt: float | None = None, *, bold: bool | None = None) -> None:
    if bold is not None:
        run.bold = bold
    if size_pt is not None:
        run.font.size = Pt(size_pt)
    run.font.name = _SONG
    run.font.color.rgb = RGBColor(0, 0, 0)
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:ascii"), _SONG)
    rfonts.set(qn("w:hAnsi"), _SONG)
    rfonts.set(qn("w:eastAsia"), _SONG)


def _set_dashed_borders(table: Table) -> None:
    tbl_pr = table._tbl.tblPr
    if tbl_pr is None:
        tbl_pr = OxmlElement("w:tblPr")
        table._tbl.insert(0, tbl_pr)
    for old in tbl_pr.findall(qn("w:tblBorders")):
        tbl_pr.remove(old)
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "dashed")
        el.set(qn("w:sz"), "18")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), "7A7A7A")
        borders.append(el)
    tbl_pr.append(borders)


def _set_row_height(row, height_cm: float) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    for old in tr_pr.findall(qn("w:trHeight")):
        tr_pr.remove(old)
    el = OxmlElement("w:trHeight")
    el.set(qn("w:val"), str(int(Cm(height_cm).twips)))
    el.set(qn("w:hRule"), "atLeast")
    tr_pr.append(el)


def _set_cell_margins(cell: _Cell, *, top: int, bottom: int, left: int, right: int) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    for old in tc_pr.findall(qn("w:tcMar")):
        tc_pr.remove(old)
    mar = OxmlElement("w:tcMar")
    for name, val in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
        node = OxmlElement(f"w:{name}")
        node.set(qn("w:w"), str(val))
        node.set(qn("w:type"), "dxa")
        mar.append(node)
    tc_pr.append(mar)
