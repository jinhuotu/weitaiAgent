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
from docx.text.paragraph import Paragraph

from api.services.tenders.schema import PlaceholderItem

logger = logging.getLogger("api.tenders")

_SONG = "宋体"
_CN_ORD = "一二三四五六七八九十"
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

# 证件类写入 Word；合同/财税等 PDF 仍占位，避免生成卡住。
_EMBED_SCAN_KEYS = frozenset({"id_legal", "id_agent"})
_SLOT_PDF_MAX_PAGES = 1
_SLOT_MAX_FILES = 2
_PLACEHOLDER_MAX_IMAGES = 8
_RENDER_SCALE = 0.5
_JPEG_QUALITY = 45
_IMAGE_MAX_PX = 800
# 身份证正反面：按 96dpi 把像素缩到实物大小，横版后叠在标题同一页
_ID_PAIR_MAX_IMAGES = 2
_ID_DISPLAY_WIDTH_CM = 7.2
_ID_DISPLAY_HEIGHT_CM = 4.6
_ID_PREVIEW_DPI = 96
_ID_ASPECT_MIN = 1.22
_ID_ASPECT_MAX = 1.98
_HEADING_CM = 1.1
_BOX_CM = 5.4
_IMAGE_MAX_H_CM = 16.0
_TWIPS_PER_CM = 567
_EMU_PER_CM = 360000.0

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
    *,
    before: Paragraph | None = None,
) -> tuple[int, int, list[str]]:
    """每个附件项：小标题 +（轻量扫描件 | 虚线框说明）。
    证件/图片写入 Word；财税/合同等 PDF 默认不渲进，避免生成卡住。
    before 为「投标承诺书」等标题时，整块插到该段之前（技术标之后）。
    返回 (已插入项数, 仍待补方框数, 限流提示)。
    """
    if not slots:
        return 0, 0, []
    slots = [item for item in slots if (item.key or "").strip() != TECH_DRAWING_KEY]
    if not slots:
        return 0, 0, []
    files_by_key = attachments or {}
    last_before = _last_body_child(doc)
    doc.add_page_break()
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_before = Pt(8)
    title.paragraph_format.space_after = Pt(12)
    head = title.add_run("附件：资料库扫描件")
    _font(head, 16, bold=True)
    filled = 0
    boxes = 0
    notes: list[str] = []
    skipped_heavy = 0
    budget = {"left": _PLACEHOLDER_MAX_IMAGES}
    for i, slot in enumerate(slots):
        key = (slot.key or "").strip()
        media = files_by_key.get(key) or []
        images = [p for p in media if p.suffix.lower() in _IMAGE_EXT]
        is_id_scan = key in _EMBED_SCAN_KEYS or "身份证" in (slot.title or "")
        embed_files = list(media) if is_id_scan else images
        if not media:
            need_cm = _HEADING_CM + _BOX_CM
        elif is_id_scan:
            need_cm = _HEADING_CM + 5.6
        elif embed_files:
            need_cm = _HEADING_CM + _first_media_cm(embed_files) + 0.4
        else:
            need_cm = _HEADING_CM + _BOX_CM
        _break_before_slot(doc, need_cm)
        _write_subheading(doc, i, slot.title)
        if not media:
            _draw_box(doc, slot)
            boxes += 1
            continue
        if embed_files:
            n = _insert_slot_media(
                doc,
                embed_files,
                budget=None if not is_id_scan else budget,
                compact_pair=is_id_scan,
                force_landscape=is_id_scan,
            )
            if n > 0:
                filled += 1
                leftover_pdf = (
                    [p for p in media if p.suffix.lower() == ".pdf"] if not is_id_scan else []
                )
                if leftover_pdf:
                    skipped_heavy += 1
                continue
        skipped_heavy += 1
        _draw_box(doc, slot)
        boxes += 1
        filled += 1

    if before is not None:
        _relocate_appended_before(doc, before=before, last_before=last_before)

    if skipped_heavy:
        notes.append(
            f"已有 {skipped_heavy} 类附件未嵌入 Word（合同/财税等仅占位提示），生成已加速；"
            "装订时请从资料库打印原件附上。证件类扫描件已写入「附件：资料库扫描件」。"
        )
    return filled, boxes, notes


def _last_body_child(doc: Document):
    body = doc.element.body
    last = None
    for child in list(body):
        if child.tag == qn("w:sectPr"):
            break
        last = child
    return last


def _relocate_appended_before(doc: Document, *, before: Paragraph, last_before) -> None:
    body = doc.element.body
    to_move = []
    passed = last_before is None
    for child in list(body):
        if child.tag == qn("w:sectPr"):
            continue
        if not passed:
            if child is last_before:
                passed = True
            continue
        to_move.append(child)
    for el in to_move:
        before._element.addprevious(el)


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


def _page_body_cm(doc: Document) -> float:
    sec = doc.sections[-1]
    return max(
        16.0,
        float(sec.page_height.cm) - float(sec.top_margin.cm) - float(sec.bottom_margin.cm) - 0.6,
    )


def _drawing_cm(el) -> float:
    total = 0.0
    for node in el.iter():
        if node.tag.split("}")[-1] != "extent":
            continue
        cy = node.get("cy")
        if cy:
            total += int(cy) / _EMU_PER_CM
    return total


def _tbl_cm(tbl) -> float:
    total = 0.0
    for tr in tbl.findall(qn("w:tr")):
        row_cm = 0.0
        tr_pr = tr.find(qn("w:trPr"))
        if tr_pr is not None:
            height = tr_pr.find(qn("w:trHeight"))
            if height is not None:
                raw = height.get(qn("w:val"))
                if raw:
                    row_cm = int(raw) / _TWIPS_PER_CM
        total += row_cm if row_cm > 0 else 0.72
    return max(total, 0.72)


def _used_on_page_cm(doc: Document) -> float:
    used = 0.0
    for child in doc.element.body:
        if child.tag == qn("w:sectPr"):
            continue
        if child.tag == qn("w:tbl"):
            used += _tbl_cm(child)
            continue
        if child.tag != qn("w:p"):
            continue
        if any(br.get(qn("w:type")) == "page" for br in child.iter(qn("w:br"))):
            used = 0.35
            continue
        p_pr = child.find(qn("w:pPr"))
        if p_pr is not None and p_pr.find(qn("w:sectPr")) is not None:
            used = 0.35
        drawn = _drawing_cm(child)
        if drawn:
            used += min(drawn + 0.35, 20.0)
            continue
        text = "".join(node.text or "" for node in child.iter(qn("w:t"))).replace("\u200b", "").strip()
        if p_pr is not None:
            sp = p_pr.find(qn("w:spacing"))
            if sp is not None and (sp.get(qn("w:lineRule")) or "") == "exact":
                line = int(sp.get(qn("w:line")) or 0)
                if line:
                    used += line / _TWIPS_PER_CM
                    continue
        if not text:
            used += 0.22
            continue
        lines = max(1, (len(text) + 29) // 30)
        used += 0.48 * lines + 0.22
    return used


def _first_media_cm(files: list[Path], *, width_cm: float = 15.5) -> float:
    for path in files:
        suf = path.suffix.lower()
        if suf == ".pdf":
            return min(_IMAGE_MAX_H_CM, width_cm * 297 / 210)
        if suf in _IMAGE_EXT and path.is_file():
            try:
                from PIL import Image

                with Image.open(path) as image:
                    w, h = image.size
                if w > 0:
                    return min(_IMAGE_MAX_H_CM, max(3.5, width_cm * h / float(w)))
            except Exception:
                return 10.0
    return _BOX_CM


def _break_before_slot(doc: Document, need_cm: float) -> None:
    """标题和附件必须同页：剩余高度不够时，标题改到下一页开头。"""
    used = _used_on_page_cm(doc)
    if used <= 1.2:
        return
    remain = _page_body_cm(doc) - used
    if remain + 0.2 >= need_cm:
        return
    doc.add_page_break()


def _write_subheading(doc: Document, index: int, title: str) -> None:
    para = doc.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    para.paragraph_format.space_before = Pt(6)
    para.paragraph_format.space_after = Pt(6)
    para.paragraph_format.keep_with_next = True
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
    compact_pair: bool = False,
    force_landscape: bool = False,
) -> int:
    if compact_pair:
        prepared = _collect_scan_jpegs(
            files,
            budget=budget,
            max_images=_ID_PAIR_MAX_IMAGES,
            max_pages=max(_SLOT_PDF_MAX_PAGES, _ID_PAIR_MAX_IMAGES),
            force_landscape=force_landscape,
        )
        return _insert_id_cards(doc, prepared)

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


def _apply_exif(image):
    try:
        from PIL import ImageOps

        transposed = ImageOps.exif_transpose(image)
        return transposed if transposed is not None else image
    except Exception:
        return image


def _looks_like_id_card(width: int, height: int) -> bool:
    short = min(width, height)
    long = max(width, height)
    if short <= 0:
        return False
    ratio = long / float(short)
    return _ID_ASPECT_MIN <= ratio <= _ID_ASPECT_MAX


def _force_landscape(image):
    from PIL import Image

    w, h = image.size
    if h <= w:
        return image
    rot = Image.Transpose.ROTATE_90 if hasattr(Image, "Transpose") else Image.ROTATE_90
    return image.transpose(rot)


def _normalize_id_image(image, *, force_landscape: bool):
    image = _apply_exif(image).convert("RGB")
    w, h = image.size
    if force_landscape or _looks_like_id_card(w, h):
        image = _force_landscape(image)
    return _shrink_pil(image)


def _image_to_jpeg(image) -> io.BytesIO:
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=max(_JPEG_QUALITY, 55), optimize=False)
    buf.seek(0)
    return buf


def _fit_cm(px_w: int, px_h: int, *, max_w: float, max_h: float) -> tuple[float, float]:
    if px_w <= 0 or px_h <= 0:
        return max_w, min(max_h, max_w * 0.63)
    aspect = px_w / float(px_h)
    width = max_w
    height = width / aspect
    if height > max_h:
        height = max_h
        width = height * aspect
    return width, height


def _collect_scan_jpegs(
    files: list[Path],
    *,
    budget: dict[str, int] | None,
    max_images: int,
    max_pages: int,
    force_landscape: bool,
) -> list[tuple[io.BytesIO, int, int]]:
    out: list[tuple[io.BytesIO, int, int]] = []
    for path in files[:_SLOT_MAX_FILES]:
        if len(out) >= max_images:
            break
        if budget is not None and budget.get("left", 0) <= 0:
            break
        suf = path.suffix.lower()
        try:
            if suf == ".pdf":
                for buf, w, h in _pdf_pages_as_jpegs(
                    path,
                    max_pages=max_pages,
                    force_landscape=force_landscape,
                    limit=max_images - len(out),
                ):
                    out.append((buf, w, h))
                    if budget is not None:
                        budget["left"] = max(0, int(budget["left"]) - 1)
                    if len(out) >= max_images:
                        break
                    if budget is not None and budget.get("left", 0) <= 0:
                        break
            elif suf in _IMAGE_EXT and path.is_file():
                from PIL import Image

                with Image.open(path) as raw:
                    image = _normalize_id_image(raw, force_landscape=force_landscape)
                    w, h = image.size
                    out.append((_image_to_jpeg(image), w, h))
                if budget is not None:
                    budget["left"] = max(0, int(budget["left"]) - 1)
        except Exception:
            logger.exception("prepare slot scan failed: %s", path)
    return out


def _pdf_pages_as_jpegs(
    pdf_path: Path,
    *,
    max_pages: int,
    force_landscape: bool,
    limit: int,
) -> list[tuple[io.BytesIO, int, int]]:
    if not pdf_path.is_file() or limit <= 0:
        return []
    try:
        import pypdfium2 as pdfium
    except ImportError:
        logger.warning("pypdfium2 missing, skip slot pdf %s", pdf_path.name)
        return []
    try:
        pdf = pdfium.PdfDocument(str(pdf_path))
    except Exception:
        logger.exception("open slot pdf failed: %s", pdf_path)
        return []
    pages: list[tuple[io.BytesIO, int, int]] = []
    count = min(len(pdf), max_pages, limit)
    for i in range(count):
        try:
            bitmap = pdf[i].render(scale=_RENDER_SCALE)
            image = _normalize_id_image(bitmap.to_pil(), force_landscape=force_landscape)
            w, h = image.size
            pages.append((_image_to_jpeg(image), w, h))
        except Exception:
            logger.exception("render slot pdf page %s of %s failed", i + 1, pdf_path.name)
    return pages


def _insert_id_cards(doc: Document, images: list[tuple[io.BytesIO, int, int]]) -> int:
    """正反面合成一张横版小图，紧跟小标题，避免预览按原图像素各占一页。"""
    if not images:
        return 0
    strip = _compose_id_strip([buf for buf, _w, _h in images[:_ID_PAIR_MAX_IMAGES]])
    if strip is None:
        return 0
    baked, width_cm, height_cm = strip
    para = doc.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    pf = para.paragraph_format
    pf.space_before = Pt(6)
    pf.space_after = Pt(8)
    pf.left_indent = Cm(0.3)
    pf.keep_with_next = False
    p_pr = para._p.get_or_add_pPr()
    for old in p_pr.findall(qn("w:pageBreakBefore")):
        p_pr.remove(old)
    para.add_run().add_picture(baked, width=Cm(width_cm), height=Cm(height_cm))
    return min(len(images), _ID_PAIR_MAX_IMAGES)


def _compose_id_strip(buffers: list[io.BytesIO]) -> tuple[io.BytesIO, float, float] | None:
    from PIL import Image

    cards: list = []
    for buf in buffers:
        try:
            buf.seek(0)
            with Image.open(buf) as raw:
                cards.append(_normalize_id_image(raw, force_landscape=True))
        except Exception:
            logger.exception("open id scan for strip failed")
    if not cards:
        return None
    resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.BICUBIC)
    card_h = 220
    prepared = []
    for im in cards:
        im = _force_landscape(im)
        w, h = im.size
        nw = max(1, int(round(card_h * w / float(h or 1))))
        prepared.append(im.resize((nw, card_h), resample))
    gap = 16
    canvas_w = sum(im.size[0] for im in prepared) + gap * (len(prepared) - 1)
    canvas = Image.new("RGB", (max(canvas_w, 1), card_h), (255, 255, 255))
    x = 0
    for im in prepared:
        canvas.paste(im, (x, 0))
        x += im.size[0] + gap
    # 两张并排约 15.2cm 宽、4.8cm 高，和标题同一页
    width_cm = 15.2 if len(prepared) > 1 else 7.4
    height_cm = width_cm * card_h / float(canvas.size[0] or 1)
    height_cm = min(height_cm, 5.2)
    px_w = max(1, int(round(width_cm / 2.54 * _ID_PREVIEW_DPI)))
    px_h = max(1, int(round(height_cm / 2.54 * _ID_PREVIEW_DPI)))
    canvas = canvas.resize((px_w, px_h), resample)
    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=82, dpi=(_ID_PREVIEW_DPI, _ID_PREVIEW_DPI), optimize=True)
    out.seek(0)
    return out, width_cm, height_cm


def _bake_id_jpeg(buf: io.BytesIO, *, width_cm: float, height_cm: float) -> io.BytesIO:
    """把图缩到与排版厘米一致的像素，docx-preview 即使用原图尺寸也不会撑满整页。"""
    from PIL import Image

    buf.seek(0)
    with Image.open(buf) as raw:
        image = raw.convert("RGB")
        px_w = max(1, int(round(width_cm / 2.54 * _ID_PREVIEW_DPI)))
        px_h = max(1, int(round(height_cm / 2.54 * _ID_PREVIEW_DPI)))
        resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.BICUBIC)
        image = image.resize((px_w, px_h), resample)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=82, dpi=(_ID_PREVIEW_DPI, _ID_PREVIEW_DPI), optimize=True)
        out.seek(0)
        return out


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
            disp_w, disp_h = _fit_cm(
                image.size[0],
                image.size[1],
                max_w=width_cm,
                max_h=_IMAGE_MAX_H_CM,
            )
            pic.add_run().add_picture(buf, width=Cm(disp_w), height=Cm(disp_h))
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
            disp_w, disp_h = _fit_cm(210, 297, max_w=15.5, max_h=_IMAGE_MAX_H_CM)
            pic.add_run().add_picture(buf, width=Cm(disp_w), height=Cm(disp_h))
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
