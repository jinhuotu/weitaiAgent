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

# 生成速度：默认不把任何扫描件渲进 Word（虚线框+说明），装订时另附原件。
# 证件类若需嵌入可再打开；当前优先保证「生成并预览」秒级返回。
_EMBED_SCAN_KEYS = frozenset()
_SLOT_PDF_MAX_PAGES = 1
_SLOT_MAX_FILES = 1
_PLACEHOLDER_MAX_IMAGES = 4
_RENDER_SCALE = 0.5
_JPEG_QUALITY = 45
_IMAGE_MAX_PX = 800

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
        hint="签约主体必须是河南伟泰光电科技有限公司。上传后自动识别项目/合同、规格型号、买方、联系人、合同额、概况与是否在建；招标优先采用已竣工充电桩项目。",
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

TECH_DRAWING_KEY = "tech_drawings"
TECH_DRAWING_SLOT = PlaceholderItem(
    key=TECH_DRAWING_KEY,
    title="实施方案图纸",
    hint="本项目平面图、系统图或施工图。换标请覆盖上传，禁止使用其他项目图纸。",
)

_PERF_NOTE = (
    "【待补】请粘贴河南伟泰光电科技有限公司作为签约主体、金额≥20万元的合同及发票。"
    "招标优先认可已竣工完成的充电桩项目。禁止使用其他公司业绩。"
)


def normalize_slot_keys(keys: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in keys or []:
        key = str(raw or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def this_bid_keys(
    *,
    extra: list[PlaceholderItem] | None = None,
    catalog: list[PlaceholderItem] | None = None,
    required_keys: list[str] | None = None,
    include_keys: list[str] | None = None,
    has_agent: bool = False,
) -> tuple[list[str], list[str]]:
    """本标必填 key、写入 Word 的 key。不把整份资料库都塞进文件。"""
    extras = extra or []
    known = {item.key for item in (catalog if catalog is not None else DEFAULT_SLOTS) if item.key}
    known.update(item.key for item in extras if item.key)

    required = normalize_slot_keys(required_keys)
    if not required:
        required = normalize_slot_keys([item.key for item in extras])
    if "id_legal" in known and "id_legal" not in required:
        required = ["id_legal", *required]
    if has_agent:
        if "id_agent" in known and "id_agent" not in required:
            required.append("id_agent")
    else:
        required = [key for key in required if key != "id_agent"]
    required = [key for key in required if key in known]

    include = normalize_slot_keys(include_keys)
    if not include:
        include = list(required)
    else:
        for key in required:
            if key not in include:
                include.append(key)
    include = [key for key in include if key in known]
    return required, include


def collect_slots(
    extra: list[PlaceholderItem] | None = None,
    *,
    catalog: list[PlaceholderItem] | None = None,
    include_keys: list[str] | None = None,
) -> list[PlaceholderItem]:
    """资料库清单 + 邀请书多出来的资料项（按 key 去重，后者可改说明）。

    include_keys 为 None 时返回合并后的全部项（资料库列表）；传入列表则只保留本标要写入 Word 的项。
    """
    base_items = catalog if catalog is not None else list(DEFAULT_SLOTS)
    by_key: dict[str, PlaceholderItem] = {
        item.key: item.model_copy() for item in base_items if item.key
    }
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
    if include_keys is None:
        return list(by_key.values())
    ordered: list[PlaceholderItem] = []
    seen: set[str] = set()
    for key in normalize_slot_keys(include_keys):
        item = by_key.get(key)
        if item is None or key in seen:
            continue
        seen.add(key)
        ordered.append(item)
    return ordered


def append_placeholder_section(
    doc: Document,
    slots: list[PlaceholderItem],
    attachments: dict[str, list[Path]] | None = None,
) -> tuple[int, int, list[str]]:
    """每个附件项：小标题 +（轻量扫描件 | 虚线框说明）。
    财税/信用/合同等大文件默认不渲进 Word，避免生成卡住。
    返回 (已插入项数, 仍待补方框数, 限流提示)。
    """
    if not slots:
        return 0, 0, []
    slots = [item for item in slots if (item.key or "").strip() != TECH_DRAWING_KEY]
    if not slots:
        return 0, 0, []
    files_by_key = attachments or {}
    doc.add_page_break()
    filled = 0
    boxes = 0
    notes: list[str] = []
    skipped_heavy = 0
    budget = {"left": _PLACEHOLDER_MAX_IMAGES}
    for i, slot in enumerate(slots):
        if i:
            doc.add_paragraph()
        _write_subheading(doc, i, slot.title)
        key = (slot.key or "").strip()
        media = files_by_key.get(key) or []
        if not media:
            _draw_box(doc, slot)
            boxes += 1
            continue

        # 大附件：只写提示 + 虚线框，不逐页渲染
        if key not in _EMBED_SCAN_KEYS:
            _write_library_skip_note(doc, slot, media)
            _draw_box(doc, slot)
            filled += 1
            skipped_heavy += 1
            continue

        if budget["left"] <= 0:
            _write_library_skip_note(doc, slot, media)
            _draw_box(doc, slot)
            filled += 1
            skipped_heavy += 1
            continue

        n = _insert_slot_media(doc, media, budget=budget)
        if n > 0:
            filled += 1
        else:
            _draw_box(doc, slot)
            boxes += 1

    if skipped_heavy:
        notes.append(
            f"已有 {skipped_heavy} 类附件未嵌入 Word（仅占位提示），生成已加速；"
            "装订时请从资料库打印原件附上。"
        )
    return filled, boxes, notes


def _write_library_skip_note(doc: Document, slot: PlaceholderItem, media: list[Path]) -> None:
    para = doc.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    names = "、".join(p.name for p in media[:3])
    more = f" 等 {len(media)} 个" if len(media) > 3 else f"（{len(media)} 个）"
    run = para.add_run(
        f"【资料库已有{more}：{names}】为加快生成未写入扫描页，装订时请附原件。"
    )
    _font(run, 10.5, bold=False)
    run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)


def draw_placeholder_box(doc: Document, slot: PlaceholderItem | None = None, *, after=None) -> None:
    """在文档中画虚线粘贴框；after 为段落时插到该段之后（用于技术标图纸）。"""
    _draw_box(doc, slot or PlaceholderItem(key="pending", title="待补资料"))
    if after is None:
        return
    body = doc.element.body
    tables = body.findall(qn("w:tbl"))
    if not tables:
        return
    after._element.addnext(tables[-1])


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


def _insert_slot_media(
    doc: Document,
    files: list[Path],
    *,
    budget: dict[str, int] | None = None,
    max_pages: int = _SLOT_PDF_MAX_PAGES,
) -> int:
    inserted = 0
    for path in files[:_SLOT_MAX_FILES]:
        if budget is not None and budget.get("left", 0) <= 0:
            break
        suf = path.suffix.lower()
        try:
            if suf == ".pdf":
                n = _insert_pdf_pages(doc, path, max_pages=max_pages, budget=budget)
                inserted += n
            elif suf in _IMAGE_EXT:
                if budget is not None and budget.get("left", 0) <= 0:
                    break
                if _insert_image(doc, path):
                    inserted += 1
                    if budget is not None:
                        budget["left"] = max(0, int(budget["left"]) - 1)
        except Exception:
            logger.exception("insert slot media failed: %s", path)
    return inserted


def _render_pdf_page_jpeg(page, *, scale: float = _RENDER_SCALE, quality: int = _JPEG_QUALITY) -> io.BytesIO:
    bitmap = page.render(scale=scale)
    image = bitmap.to_pil().convert("RGB")
    image = _shrink_pil(image)
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality, optimize=False)
    buf.seek(0)
    return buf


def _shrink_pil(image):
    try:
        w, h = image.size
        longest = max(w, h)
        if longest <= _IMAGE_MAX_PX:
            return image
        ratio = _IMAGE_MAX_PX / float(longest)
        return image.resize((max(1, int(w * ratio)), max(1, int(h * ratio))))
    except Exception:
        return image


def _insert_image(doc: Document, path: Path, *, width_cm: float = 15.5) -> bool:
    if not path.is_file():
        return False
    pic = doc.add_paragraph()
    pic.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pic.paragraph_format.space_before = Pt(4)
    pic.paragraph_format.space_after = Pt(4)
    try:
        from PIL import Image

        with Image.open(path) as raw:
            image = _shrink_pil(raw.convert("RGB"))
            buf = io.BytesIO()
            image.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=False)
            buf.seek(0)
            pic.add_run().add_picture(buf, width=Cm(width_cm))
    except Exception:
        pic.add_run().add_picture(str(path), width=Cm(width_cm))
    return True


def _insert_pdf_pages(
    doc: Document,
    pdf_path: Path,
    *,
    max_pages: int = _SLOT_PDF_MAX_PAGES,
    budget: dict[str, int] | None = None,
) -> int:
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
        if budget is not None and budget.get("left", 0) <= 0:
            break
        try:
            page = pdf[i]
            buf = _render_pdf_page_jpeg(page)
            pic = doc.add_paragraph()
            pic.alignment = WD_ALIGN_PARAGRAPH.CENTER
            pic.paragraph_format.space_before = Pt(4)
            pic.paragraph_format.space_after = Pt(4)
            pic.add_run().add_picture(buf, width=Cm(15.5))
            inserted += 1
            if budget is not None:
                budget["left"] = max(0, int(budget["left"]) - 1)
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
