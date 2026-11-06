"""Place selected library scans at typed placeholders, using inline blank paragraphs."""

from __future__ import annotations

import io
import re
from hashlib import sha1
from pathlib import Path

from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from docx.text.paragraph import Paragraph

ALIASES = {
    "id_legal": ("法定代表人身份证", "法人身份证", "法定代表人身份证明"),
    "id_agent": ("委托代理人身份证", "代理人身份证", "授权代理人身份证", "授权委托书"),
    "license": ("营业执照",),
    "finance": ("纳税", "完税", "税收", "财务", "审计报告"),
    "social": ("社保", "社会保障"),
    "perf": ("业绩", "类似项目合同", "类似项目情况", "合同及发票"),
    "bank_permit": ("开户许可", "基本账户", "基本存款账户"),
    "iso": ("ISO", "质量管理体系", "体系认证"),
    "product": ("检测报告", "3C", "产品认证", "产品证明"),
    "bond": ("投标保证金", "保证金缴存", "保函"),
    "credit": ("信用中国", "企业信用信息", "信用查询"),
    "seal": ("签章页", "印章扫描件"),
    "qualification": ("企业资质", "资质证书", "安全生产许可"),
}
_CUE = re.compile(r"复印件|扫描件|粘贴处|[此这]处粘贴|在此粘贴|附[：:]")


def _text(el):
    return "".join(n.text or "" for n in el.iter(qn("w:t")))


def _compact(text):
    return re.sub(r"\s+", "", text or "").upper()


def _prefix(key):
    return "scan_slot_" + sha1(key.encode()).hexdigest()[:12] + "_"


def _matching_files(key, anchor, files):
    """Honor explicit subtype labels when filenames distinguish a shared slot."""
    text = _text(anchor)
    if not text.strip() and anchor.getprevious() is not None:
        text = _text(anchor.getprevious())
    if key == "finance":
        tax = re.compile(r"纳税|完税|税收")
        audit = re.compile(r"财务|审计|报表")
        desired = (
            tax
            if tax.search(text) and not audit.search(text)
            else (audit if audit.search(text) and not tax.search(text) else None)
        )
        if desired is not None and any(tax.search(p.name) or audit.search(p.name) for p in files):
            return [p for p in files if desired.search(p.name)]
    if key in {"id_legal", "id_agent"}:
        if re.search(r"正.{0,3}反.{0,2}面", text):
            return files
        front = "正面" in text and "反面" not in text
        back = "反面" in text and "正面" not in text
        if front or back:
            kind = re.compile("正面|FRONT" if front else "反面|BACK", re.I)
            if any(re.search("正面|反面|FRONT|BACK", p.name, re.I) for p in files):
                return [p for p in files if kind.search(p.name)]
    return files


def _frame(el):
    return el.find("w:pPr/w:pBdr", el.nsmap) is not None


def _cell(el):
    return next((n for n in el.iterancestors(qn("w:tc"))), None)


def _cell_frame(el, *, explicit=False):
    cell = _cell(el)
    if cell is None:
        return False
    table = next(cell.iterancestors(qn("w:tbl")), None)
    return cell.find("w:tcPr/w:tcBorders", cell.nsmap) is not None or (
        table is not None
        and (
            table.find("w:tblPr/w:tblBorders", table.nsmap) is not None
            or (not explicit and len(list(table.iter(qn("w:tc")))) == 1)
        )
    )


def _heading(el, doc):
    from api.services.tenders.native_format import _physical_chapter

    text = _text(el)
    return not _CUE.search(text) and _physical_chapter(el, doc)


def _width(doc, anchor):
    sec = doc.sections[-1]
    width = (sec.page_width - sec.left_margin - sec.right_margin) / 360000 - 0.4
    cell = _cell(anchor)
    if cell is not None:
        w = cell.find("w:tcPr/w:tcW", cell.nsmap)
        if w is not None and w.get(qn("w:type")) == "dxa":
            width = min(width, int(w.get(qn("w:w"), "0")) * 2.54 / 1440 - 0.4)
    return max(0.8, width)


def _pictures(doc, anchor, files, key=""):
    """Create inline paragraphs in this document (retaining its image relations)."""
    from PIL import Image, ImageOps

    from api.services.tenders.placeholders import (
        _compose_id_strip,
        _fit_cm,
        _normalize_id_image,
        _render_pdf_page_bytes,
        _scan_height_cm,
    )

    sec = doc.sections[-1]
    max_h = min(
        _scan_height_cm(doc), (sec.page_height - sec.top_margin - sec.bottom_margin) / 360000 - 1
    )
    paragraphs = []
    errors = []
    cards = []

    def picture(stream, size, full_page=False):
        if key in {"id_legal", "id_agent"}:
            with Image.open(stream) as raw:
                card = _normalize_id_image(raw, force_landscape=True)
                buffer = io.BytesIO()
                card.save(buffer, format="JPEG", quality=90)
                buffer.seek(0)
                cards.append(buffer)
            return
        insert(stream, size, _width(doc, anchor), max_h, full_page=full_page or size[1] > size[0])

    def insert(stream, size, width, height, full_page=False):
        w, h = _fit_cm(*size, max_w=width, max_h=height)
        from api.services.cablepkg.image_quality import image_png
        with Image.open(stream) as source:
            stream = image_png(source, width_cm=w, height_cm=h)
        p = doc.add_paragraph(style="Normal")
        p.paragraph_format.page_break_before = full_page
        p.paragraph_format.line_spacing = 1
        p.paragraph_format.space_before = p.paragraph_format.space_after = Pt(3)
        p.paragraph_format.keep_with_next = False
        p.paragraph_format.keep_together = True
        p.alignment = 1
        try:
            p.add_run().add_picture(stream, width=Cm(w), height=Cm(h))
        except Exception:
            p._p.getparent().remove(p._p)
            raise
        paragraphs.append(p._p)

    for path in files:
        try:
            if path.suffix.lower() == ".pdf":
                import pypdfium2 as pdfium

                pdf = pdfium.PdfDocument(str(path))
                try:
                    for n in range(len(pdf)):
                        page = pdf[n]
                        try:
                            stream = _render_pdf_page_bytes(page, sharp=True)
                            with Image.open(stream) as img:
                                size = img.size
                            stream.seek(0)
                            picture(stream, size, full_page=True)
                        finally:
                            page.close()
                finally:
                    pdf.close()
            else:
                with Image.open(path) as raw:
                    image = ImageOps.exif_transpose(raw).convert("RGB")
                    stream = io.BytesIO()
                    image.save(stream, format="PNG")
                    stream.seek(0)
                    picture(stream, image.size)
        except Exception:  # A bad scan must not erase its placeholder or other valid pages.
            errors.append(path.name)
    for n in range(0, len(cards), 2):
        strip = _compose_id_strip(cards[n : n + 2])
        if strip is not None:
            stream, width, height = strip
            with Image.open(stream) as image:
                size = image.size
            stream.seek(0)
            insert(stream, size, min(_width(doc, anchor), width), min(5.4, height))
    return paragraphs, errors


def _defer_boundary(doc, anchor, cursor):
    """Keep attachments before an end-of-chapter page/section boundary."""
    if anchor.tag != qn("w:p"):
        return
    section = anchor.find("w:pPr/w:sectPr", anchor.nsmap)
    nodes = list(anchor.iter())
    breaks = [
        node
        for n, node in enumerate(nodes)
        if node.tag == qn("w:br")
        and node.get(qn("w:type")) == "page"
        and not any(
            other.tag == qn("w:t") and (other.text or "").strip() for other in nodes[n + 1 :]
        )
    ]
    if section is None and not breaks:
        return
    carrier = doc.add_paragraph(style="Normal")
    if section is not None:
        section.getparent().remove(section)
        carrier._p.get_or_add_pPr().append(section)
    for node in breaks:
        node.getparent().remove(node)
        carrier.add_run()._r.append(node)
    cursor.addnext(carrier._p)


def place_library_scans(doc, brief=None, media=None, slots=None, skip_keys=None, global_pasted: set[str] | None = None, volume=''):
    from api.services.tenders.section_materials import append_section_materials
    from api.services.tenders.pdf_format import toc_block_ids
    notes, handled = append_section_materials(
        doc, slots or [], media,
        list(media or {}), toc_block_ids(doc),
        set(skip_keys or ()) | set(global_pasted or ()),
        auth_enabled=brief.authEnabled if brief is not None else None,
    )
    if global_pasted is not None:
        global_pasted.update(handled)
    return notes, handled
