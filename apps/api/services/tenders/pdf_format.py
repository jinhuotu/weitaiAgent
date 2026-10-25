"""招标书格式章：从上传的 Word 里截出范本，只填对得上的空。"""

from __future__ import annotations

import logging
import re
import shutil
from copy import deepcopy
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from api.services.tenders.assets import tender_invitations_dir
from api.services.tenders.outline import _FORMAT_HEAD, _slice_format

logger = logging.getLogger("api.tenders.pdf_format")

_PAGE_TAG = re.compile(r"\[第\s*(\d+)\s*页\]")
_MAX_PAGES = 100


def format_page_span(text: str) -> tuple[int, int] | None:
    """格式章起止页。目录里也会出现标题，取页码更靠后的那一次。"""
    raw = text or ""
    matches = list(_FORMAT_HEAD.finditer(raw))
    if not matches:
        return None
    best = None
    start = -1
    for match in matches:
        pages = [int(n) for n in _PAGE_TAG.findall(raw[: match.start()])]
        page = pages[-1] if pages else 0
        if page >= start:
            best = match
            start = page
    if best is None or start <= 0:
        return None
    _title, body = _slice_format(raw, best)
    inside = [int(n) for n in _PAGE_TAG.findall(body)]
    end = inside[-1] if inside else start
    if end < start:
        end = start
    if end - start + 1 > _MAX_PAGES:
        end = start + _MAX_PAGES - 1
    return start, end


def format_docx_path(invitation_id: str) -> Path | None:
    invite_id = (invitation_id or "").strip()
    if not re.fullmatch(r"[a-f0-9]{10,16}", invite_id):
        return None
    return tender_invitations_dir() / f"{invite_id}.format.docx"


def prepare_format_docx(pdf: Path, text: str, invitation_id: str) -> str:
    """PDF 先按版面转成 Word，再按 Word 原件的规则切格式章。"""
    del text
    if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
        return "请上传 PDF 或 Word"
    word = pdf.with_suffix(".docx")
    try:
        from api.services.tenders.pdf_to_docx import convert_pdf_to_docx

        how = convert_pdf_to_docx(pdf, word)
    except Exception as exc:  # noqa: BLE001
        logger.warning("pdf to docx failed: %s", exc)
        return f"PDF 转 Word 失败：{exc}"
    note = prepare_word_format_docx(word, invitation_id)
    if not note:
        return how
    if "失败" not in note and "没有抽到" not in note:
        marked = format_docx_path(invitation_id)
        if marked is not None:
            _format_source_mark(marked).write_text("pdf", encoding="utf-8")
    return f"{how}。{note}"


_NOISE_LINE = re.compile(r"^-?\d{1,4}-?$")
_HEADING_LINE = re.compile(
    r"^(?:第[一二三四五六七八九十0-9]+[章节部分]|[一二三四五六七八九十]{1,2}、|[（(][一二三四五六七八九十0-9]+[)）])"
)


def format_layout(invitation_id: str, *, volume: str) -> tuple[list, list, str]:
    """封面用招标书里的，目录不带过去。返回 (封面元素, 正文元素, 说明)。"""
    if (volume or "").strip() == "technical":
        return [], [], ""
    src = _ready_format_docx(invitation_id)
    if src is None:
        return [], [], ""
    cover, body, tables = _pick_format_parts(Document(str(src)))
    if not cover and not body:
        return [], [], ""
    note = f"已用招标书封面，目录按页码生成，正文保留 {tables} 张表"
    return cover, body, note


def paste_invite_format(doc: Document, invitation_id: str, *, volume: str) -> str:
    """按标书大纲时，把转好的格式章插进正文。技术标不整章粘贴。"""
    cover, body, note = format_layout(invitation_id, volume=volume)
    if not cover and not body:
        return ""
    append_elements(doc, [*cover, *body])
    return note or "正文已插入格式章转档"


def _ready_format_docx(invitation_id: str) -> Path | None:
    return _word_format_file(invitation_id)


def append_elements(doc: Document, elements: list) -> None:
    body = doc.element.body
    sect = body.find(qn("w:sectPr"))
    for el in elements:
        node = deepcopy(el)
        if sect is not None:
            sect.addprevious(node)
        else:
            body.append(node)


def append_format_docx(doc: Document, src: Path) -> int:
    _cover, body, tables = _pick_format_parts(Document(str(src)))
    append_elements(doc, body)
    return tables


def _pick_format_parts(src: Document) -> tuple[list, list, int]:
    """丢掉空白页、只有标题的页、招标书自带目录。封面单独取出。"""
    cover: list = []
    body: list = []
    tables = 0
    for page in _split_pages(list(src.element.body)):
        kind = _page_kind(page)
        if kind in {"blank", "title", "toc"}:
            continue
        # 封面后面组装会自己换节，源文件的换页分节留着会多出一页空白
        nodes = _copy_page(page, keep_page_sect=(kind != "cover"))
        if not nodes:
            continue
        if kind == "cover" and not cover:
            cover.extend(nodes)
            continue
        if kind == "cover":
            continue
        body.extend(nodes)
        tables += sum(1 for el in nodes if el.tag == qn("w:tbl"))
    return cover, body, tables


def _split_pages(children: list) -> list[list]:
    # pdf2docx 每一页是 nextPage 分节，不是 w:br 分页
    pages: list[list] = []
    cur: list = []
    for el in children:
        if el.tag == qn("w:sectPr"):
            continue
        role = _break_role(el)
        if role == "end" or _page_sect(el) is not None:
            if _keep_on_page(el):
                cur.append(el)
            if cur:
                pages.append(cur)
            cur = []
            continue
        if role == "start":
            if cur:
                pages.append(cur)
            cur = []
            if _el_text(el).strip() or el.tag == qn("w:tbl"):
                cur.append(el)
            continue
        cur.append(el)
    if cur:
        pages.append(cur)
    return pages


def _page_sect(el):
    if el.tag != qn("w:p"):
        return None
    ppr = el.find(qn("w:pPr"))
    if ppr is None:
        return None
    sect = ppr.find(qn("w:sectPr"))
    if sect is None:
        return None
    typ = sect.find(qn("w:type"))
    val = (typ.get(qn("w:val")) if typ is not None else "") or "nextPage"
    if val in {"continuous", "nextColumn"}:
        return None
    return sect


def _keep_on_page(el) -> bool:
    if _el_text(el).strip() or el.tag == qn("w:tbl"):
        return True
    if next(el.iter(qn("w:drawing")), None) is not None:
        return True
    return _page_sect(el) is not None


def _is_noise_para(el) -> bool:
    if el.tag != qn("w:p"):
        return False
    compact = re.sub(r"\s+", "", _el_text(el))
    return bool(compact) and _NOISE_LINE.fullmatch(compact) is not None


def _copy_page(page: list, *, keep_page_sect: bool) -> list:
    out = []
    for el in page:
        node = deepcopy(el)
        if _is_noise_para(node):
            continue
        if not keep_page_sect and _drop_page_sect(node):
            continue
        out.append(node)
    return out


def _drop_page_sect(el) -> bool:
    """去掉换页分节。空段返回 True。"""
    sect = _page_sect(el)
    if sect is None:
        return False
    ppr = el.find(qn("w:pPr"))
    ppr.remove(sect)
    if _el_text(el).strip() or next(el.iter(qn("w:drawing")), None) is not None:
        return False
    return True


def _break_role(el) -> str:
    seen_text = False
    for node in el.iter():
        if node.tag == qn("w:t") and (node.text or "").strip():
            seen_text = True
        if node.tag == qn("w:lastRenderedPageBreak"):
            return "start" if not seen_text else "end"
        if node.tag == qn("w:br") and (node.get(qn("w:type")) or "") == "page":
            return "start" if not seen_text else "end"
    return ""


def _page_kind(page: list) -> str:
    if any(el.tag == qn("w:tbl") for el in page):
        text = _page_text(page)
        if _is_cover(text) and "我方已仔细研究" not in text:
            return "cover"
        return "body"
    lines = _meaningful_lines(_page_text(page))
    if not lines:
        return "blank"
    blob = "\n".join(lines)
    if _is_source_toc(lines):
        return "toc"
    if _is_cover(blob):
        return "cover"
    if _is_title_only(lines):
        return "title"
    return "body"


def _page_text(page: list) -> str:
    return "\n".join(_el_text(el) for el in page)


def _el_text(el) -> str:
    parts: list[str] = []
    for node in el.iter(qn("w:t")):
        if node.text:
            parts.append(node.text)
    return "".join(parts)


def _meaningful_lines(text: str) -> list[str]:
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        compact = re.sub(r"\s+", "", line)
        if _NOISE_LINE.fullmatch(compact):
            continue
        if "基础设施集群项目" in compact or compact.startswith("郑州高新技术"):
            continue
        if len(compact) <= 1:
            continue
        out.append(line)
    return out


def _is_cover(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    if "商务标" in compact or "技术标" in compact:
        return False
    if "我方已仔细研究" in compact or compact.startswith("目录"):
        return False
    # 授权委托书等正文里也有「投标文件」「盖单位章」，不能当成第二张封面丢掉
    if _HEADING_LINE.match(compact) or any(k in compact for k in ("授权委托", "身份证明", "投标函", "承诺书")):
        return False
    if "投标文件" not in compact:
        return False
    return any(k in compact for k in ("招标编号", "盖单位章", "联合体牵头人", "签字或盖章"))


def _is_source_toc(lines: list[str]) -> bool:
    if not lines or re.sub(r"\s+", "", lines[0]) != "目录":
        return False
    heads = 0
    for line in lines[1:]:
        if _HEADING_LINE.match(re.sub(r"\s+", "", line)):
            heads += 1
    return heads >= 2


def _is_title_only(lines: list[str]) -> bool:
    """格式章扉页，例如「第八章、投标文件格式」。章内的「六、项目管理机构」要留。"""
    if len(lines) != 1:
        return False
    compact = re.sub(r"\s+", "", lines[0])
    return "格式" in compact and re.match(r"^第[0-9一二三四五六七八九十]+章", compact) is not None


def fill_pasted_format(doc: Document, brief, media: dict | None = None) -> None:
    """封面横线按招标书原位补上，并把单位/项目/电话地址填进空位。"""
    from api.services.tenders.document import (
        _add_form_pad,
        _clear_runs,
        _form_run,
        _set_word_wrap,
        _ymd,
    )

    _flatten_split_tables(doc, brief, _clear_runs, _form_run)
    paras = list(doc.paragraphs)
    toc_i = len(paras)
    for i, para in enumerate(paras):
        if re.sub(r"\s+", "", para.text or "") == "目录":
            toc_i = i
            break
    for para in paras[:toc_i]:
        _fix_cover_line(para, brief, _clear_runs, _form_run, _add_form_pad, _ymd)
    for para in paras[toc_i + 1 :]:
        _swap_tokens(para._p, brief)
        _fill_sign_para(para, brief, _clear_runs, _form_run, _set_word_wrap)
        _fill_short_sign(para, brief, _clear_runs, _form_run, _set_word_wrap)
        _fill_plain_date(para, brief, _clear_runs, _form_run, _ymd)
        _fill_contact_para(para, brief, _clear_runs, _form_run, _ymd)
        _fill_identity_para(para, brief, _clear_runs, _form_run)
        _cap_sign_gap(para)
    for table in list(doc.tables):
        _swap_tokens(table._tbl, brief)
        _fill_table(table, brief)
    _paste_id_pages(doc, brief, media)


def _fix_cover_line(para, brief, clear_runs, form_run, add_pad, ymd) -> None:
    n = re.sub(r"\s+", "", para.text or "")
    if not n or re.fullmatch(r"-?\d{1,4}-?", n):
        return
    if "项目名称" in n and "标段施工" in n:
        _cover_center(para, clear_runs)
        name = (brief.projectName or "").strip()
        if name:
            form_run(para, name, underline=True)
        else:
            add_pad(para, 14)
        form_run(para, "（项目名称）", underline=False)
        add_pad(para, 4)
        form_run(para, "标段施工", underline=False)
        return
    if n.startswith("招标编号"):
        _cover_field(para, "招标编号：", (brief.tenderNo or "").strip(), 8, clear_runs, form_run, add_pad)
        return
    if "联合体牵头人" in n:
        _cover_field(
            para,
            "投标人或联合体牵头人：",
            (brief.bidderName or "").strip(),
            12,
            clear_runs,
            form_run,
            add_pad,
        )
        return
    if n.startswith("法定代表人"):
        _cover_center(para, clear_runs)
        form_run(para, "法定代表人或其委托代理人：", underline=False)
        add_pad(para, 6)
        form_run(para, "（签字或盖章）", underline=False)
        return
    if "年" in n and "月" in n and n.endswith("日") and "投标" not in n and len(n) <= 24:
        year, month, day = ymd(brief.bidDate or "")
        _cover_center(para, clear_runs)
        for part, lab in ((year, "年"), (month, "月"), (day, "日")):
            shown = part if (part or "").strip("　 ") else "\u3000\u3000"
            form_run(para, shown, underline=True)
            form_run(para, lab, underline=False)


def _cover_center(para, clear_runs) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm

    clear_runs(para)
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pf = para.paragraph_format
    pf.left_indent = Cm(0)
    pf.right_indent = Cm(0)
    pf.first_line_indent = Cm(0)


def _cover_field(para, label, value, blank, clear_runs, form_run, add_pad) -> None:
    _cover_center(para, clear_runs)
    form_run(para, label, underline=False)
    if value:
        form_run(para, value, underline=True)
        return
    add_pad(para, blank)


def _swap_tokens(el, brief) -> None:
    pairs = []
    for needle, value in (
        ("（项目名称）", (brief.projectName or "").strip()),
        ("(项目名称)", (brief.projectName or "").strip()),
        ("（招标人名称）", (brief.tenderer or "").strip()),
        ("（投标人名称）", (brief.bidderName or "").strip()),
        ("（采购人名称）", (brief.tenderer or "").strip()),
    ):
        if value:
            pairs.append((needle, value))
    if not pairs:
        return
    for node in el.iter(qn("w:t")):
        if not node.text:
            continue
        s = node.text
        for needle, value in pairs:
            if needle in s:
                s = s.replace(needle, value)
        node.text = s


def _fill_sign_para(para, brief, clear_runs, form_run, set_wrap) -> None:
    raw = para.text or ""
    compact = re.sub(r"\s+", "", raw)
    if not compact.startswith("投标人") or "盖单位" not in compact or len(compact) > 90:
        return
    bidder = (brief.bidderName or "").strip()
    if bidder and bidder in raw:
        return
    has_legal = "法定代表人" in compact
    seal = "（盖单位章）" if "盖单位章" in compact else "（盖单位公章）"
    clear_runs(para)
    form_run(para, "投标人：", underline=False)
    if bidder:
        form_run(para, bidder, underline=True)
    else:
        form_run(para, "\u3000" * 8, underline=True)
    form_run(para, seal, underline=False)
    set_wrap(para, enabled=False)
    if not has_legal:
        return
    para.add_run().add_break()
    form_run(para, "法定代表人或其委托代理人：", underline=False)
    form_run(para, "\u3000" * 6, underline=True)
    form_run(para, "（签字或盖章）", underline=False)


def _fill_contact_para(para, brief, clear_runs, form_run, ymd) -> None:
    raw = para.text or ""
    compact = re.sub(r"\s+", "", raw)
    if "邮政编码" not in compact or ("邮箱" not in compact and "邮编" not in compact and "年" not in compact):
        return
    if len(compact) > 80 and "投标函附录" not in compact:
        return
    tail = ""
    for mark in ("（二）", "(二)"):
        idx = raw.find(mark)
        if idx >= 0:
            tail = raw[idx:]
            break
    clear_runs(para)

    def field(label, value):
        form_run(para, label, underline=False)
        text = (value or "").strip()
        if text:
            form_run(para, text, underline=True)
        else:
            form_run(para, "\u3000" * 6, underline=True)
        form_run(para, "    ", underline=False)

    field("邮政编码：", brief.bidderPostcode)
    field("电话：", brief.bidderPhone)
    field("邮箱：", brief.bidderEmail)
    year, month, day = ymd(brief.bidDate or "")
    for part, lab in ((year, "年"), (month, "月"), (day, "日")):
        shown = part if (part or "").strip("　 ") else "\u3000\u3000"
        form_run(para, shown, underline=True)
        form_run(para, lab, underline=False)
    if tail:
        para.add_run().add_break()
        form_run(para, tail, underline=False)


def _fill_identity_para(para, brief, clear_runs, form_run) -> None:
    compact = re.sub(r"\s+", "", para.text or "")
    if "姓名" not in compact or "性别" not in compact or "法定代表人" not in compact:
        return
    if len(compact) > 120:
        return
    bidder = (brief.bidderName or "").strip()
    legal = (brief.legalPersonName or "").strip()
    gender = (brief.legalPersonGender or "").strip()
    age = (brief.legalPersonAge or "").strip()
    title = (brief.legalPersonTitle or "").strip()
    clear_runs(para)
    form_run(para, "投标人名称：", underline=False)
    form_run(para, bidder or "\u3000" * 6, underline=bool(bidder))
    form_run(para, "    姓名：", underline=False)
    form_run(para, legal or "\u3000" * 3, underline=bool(legal))
    form_run(para, "    性别：", underline=False)
    form_run(para, gender or "\u3000", underline=bool(gender))
    form_run(para, "    年龄：", underline=False)
    form_run(para, age or "\u3000" * 2, underline=bool(age))
    form_run(para, "    职务：", underline=False)
    form_run(para, title or "\u3000" * 3, underline=bool(title))
    para.add_run().add_break()
    form_run(para, "系", underline=False)
    form_run(para, bidder or "\u3000" * 4, underline=bool(bidder))
    form_run(para, "的法定代表人。特此证明。", underline=False)


_CELL_LABELS = {
    "投标人名称": "bidderName",
    "投标人": "bidderName",
    "项目名称": "projectName",
    "注册地址": "bidderAddress",
    "地址": "bidderAddress",
    "邮政编码": "bidderPostcode",
    "电话": "bidderPhone",
    "联系电话": "bidderPhone",
    "传真": "bidderFax",
    "网址": "bidderWebsite",
    "邮箱": "bidderEmail",
    "法定代表人": "legalPersonName",
    "项目经理": "pmName",
}


def _fill_table(table, brief) -> None:
    for row in table.rows:
        seen = set()
        seq = []
        for cell in row.cells:
            if id(cell._tc) in seen:
                continue
            seen.add(id(cell._tc))
            seq.append(cell)
        if not seq:
            continue
        plain = re.sub(r"\s+", "", "".join(c.text or "" for c in seq))
        if re.fullmatch(r"电话[：:]?", plain):
            _put_tail(seq, brief.bidderPhone, "电话：")
            continue
        if re.fullmatch(r"传真[：:]?", plain):
            _put_tail(seq, brief.bidderFax, "传真：")
            continue
        # 投标函里「地址」被拆成「地网 / 址：」。发包人地址、注册地址不要走这里。
        if re.fullmatch(r"地[网]?址[：:址]*", plain):
            _put_tail(seq, brief.bidderAddress, "地址：")
            continue
        for i, cell in enumerate(seq[:-1]):
            key = re.sub(r"\s+", "", cell.text or "").rstrip("：:")
            attr = _CELL_LABELS.get(key)
            if not attr:
                continue
            if key == "电话" and any(k in plain for k in ("技术负责人", "项目经理", "发包人", "监理")):
                continue
            value = (getattr(brief, attr, "") or "").strip()
            if not value:
                continue
            nxt = seq[i + 1]
            if re.sub(r"\s+", "", nxt.text or ""):
                continue
            nxt.text = value


def _put_tail(seq, value, label: str) -> None:
    text = (value or "").strip()
    if not text:
        return
    last = seq[-1]
    if text in (last.text or ""):
        return
    cur = re.sub(r"\s+", "", last.text or "")
    if cur in {"", "：", ":"}:
        last.text = text
        return
    if cur.endswith("：") or cur.endswith(":"):
        last.text = (last.text or "").rstrip() + text
        return
    last.text = label + text


def _flatten_split_tables(doc: Document, brief, clear_runs, form_run) -> None:
    """pdf2docx 把下划线画成带边框的表。改回一行字加下划线，地址拆成两行。"""
    from docx.text.paragraph import Paragraph

    project = (brief.projectName or "").strip()
    body = doc.element.body
    for tbl in list(body.findall(qn("w:tbl"))):
        bits = [_cell_plain(tc) for tr in tbl.findall(qn("w:tr")) for tc in tr.findall(qn("w:tc"))]
        bits = [b for b in bits if b]
        blob = "".join(bits)
        if re.fullmatch(r"地[网]?址[：:址]*", blob):
            _swap_table_for_lines(tbl, doc, brief, form_run)
            continue
        if not any(k in blob for k in ("撤回", "我方已仔细研究了", "拟派往")):
            continue
        if "投标总报价" in blob or len(blob) > 80:
            continue
        nxt = tbl.getnext()
        while nxt is not None and nxt.tag == qn("w:p") and not _el_text(nxt).strip():
            nxt = nxt.getnext()
        prefix = bits[0] if bits else ""
        prefix = re.sub(r"[（(]项目名称[)）]", "", prefix).rstrip("：: ")
        if prefix and not prefix.endswith(("，", "。", "、", "了", "往")):
            prefix += ""
        rest = ""
        host = None
        if nxt is not None and nxt.tag == qn("w:p"):
            rest = _el_text(nxt).strip()
            if rest.startswith("标段"):
                host = Paragraph(nxt, doc)
        if host is None:
            from docx.oxml import OxmlElement

            new_p = OxmlElement("w:p")
            tbl.addprevious(new_p)
            host = Paragraph(new_p, doc)
            rest = ""
        clear_runs(host)
        if prefix:
            form_run(host, prefix, underline=False)
            form_run(host, " ", underline=False)
        if project:
            form_run(host, project, underline=True)
        elif "项目名称" in blob:
            form_run(host, "（项目名称）", underline=True)
        if rest:
            if not rest.startswith(("标段", " ", "，")):
                form_run(host, " ", underline=False)
            form_run(host, rest, underline=False)
        parent = tbl.getparent()
        if parent is not None:
            junk = tbl.getnext()
            parent.remove(tbl)
            while junk is not None and junk is not host._p:
                nxt_junk = junk.getnext()
                if junk.tag == qn("w:p") and not _el_text(junk).strip():
                    parent.remove(junk)
                junk = nxt_junk


def _cell_plain(tc) -> str:
    return re.sub(r"\s+", "", "".join(t.text or "" for t in tc.iter(qn("w:t"))))


def _swap_table_for_lines(tbl, doc: Document, brief, form_run) -> None:
    from docx.oxml import OxmlElement
    from docx.text.paragraph import Paragraph

    anchor = tbl
    lines = (
        ("地    址：", (brief.bidderAddress or "").strip()),
        ("网    址：", (brief.bidderWebsite or "").strip()),
    )
    for label, value in reversed(lines):
        new_p = OxmlElement("w:p")
        anchor.addprevious(new_p)
        para = Paragraph(new_p, doc)
        form_run(para, label, underline=False)
        if value:
            form_run(para, value, underline=True)
        else:
            form_run(para, "\u3000" * 10, underline=True)
        anchor = new_p
    parent = tbl.getparent()
    if parent is not None:
        parent.remove(tbl)


def _fill_short_sign(para, brief, clear_runs, form_run, set_wrap) -> None:
    raw = para.text or ""
    n = re.sub(r"\s+", "", raw)
    if len(n) > 28 or "系" in n or n.startswith("投标人"):
        return
    spec = None
    if n.startswith("委托期限"):
        spec = ("委托期限：", "", "")
    elif n.startswith("身份证号码"):
        spec = ("身份证号码：", "", "")
    elif n.startswith("委托代理人"):
        spec = ("委托代理人：", "", "（签字或盖章）" if "签字" in n else "")
    elif n.startswith("法定代表人"):
        spec = ("法定代表人：", "", "（签字或盖章）" if "签字" in n else "")
    if spec is None:
        return
    label, _value, suffix = spec
    clear_runs(para)
    form_run(para, label, underline=False)
    form_run(para, "\u3000" * 8, underline=True)
    if suffix:
        form_run(para, suffix, underline=False)
    set_wrap(para, enabled=False)
    _ = brief


def _fill_plain_date(para, brief, clear_runs, form_run, ymd) -> None:
    n = re.sub(r"\s+", "", para.text or "")
    if n != "年月日":
        return
    year, month, day = ymd(brief.bidDate or "")
    clear_runs(para)
    for part, lab in ((year, "年"), (month, "月"), (day, "日")):
        shown = part if (part or "").strip("　 ") else "\u3000\u3000"
        form_run(para, shown, underline=True)
        form_run(para, lab, underline=False)


def _cap_sign_gap(para) -> None:
    n = re.sub(r"\s+", "", para.text or "")
    if len(n) > 40:
        return
    if not any(n.startswith(k) for k in ("投标人", "法定代表人", "委托代理人", "身份证号码", "委托期限")):
        return
    ppr = para._p.find(qn("w:pPr"))
    if ppr is None:
        return
    spacing = ppr.find(qn("w:spacing"))
    if spacing is None:
        return
    before = spacing.get(qn("w:before"))
    if before and before.isdigit() and int(before) > 280:
        spacing.set(qn("w:before"), "160")


def _paste_id_pages(doc: Document, brief, media: dict | None) -> str:
    from api.services.tenders.placeholders import (
        _insert_slot_media,
        _last_body_child,
        _relocate_appended_before,
    )

    files = [p for p in ((media or {}).get("id_legal") or []) if getattr(p, "is_file", lambda: False)()]
    who = (getattr(brief, "legalPersonName", "") or "").strip() or "所选人员"
    toc_els = toc_block_ids(doc)
    paras = list(doc.paragraphs)
    for i, para in enumerate(paras):
        if para._p in toc_els:
            continue
        n = re.sub(r"\s+", "", para.text or "")
        # 身份证明页才贴。授权委托书那句带「委托」，不在这里再插一张，避免又空出一截。
        if not n.startswith("附") or "法定代表人身份证" not in n or "委托" in n:
            continue
        if not files:
            _dash_after(doc, para, "id_legal", n)
            return f"截件有法定代表人身份证位置，未贴上{who}的扫描件"
        before = next(
            (p for p in paras[i + 1 :] if re.sub(r"\s+", "", p.text or "").startswith("投标人")),
            None,
        )
        last = _last_body_child(doc)
        n_img = _insert_slot_media(doc, files, compact_pair=True)
        if n_img and before is not None:
            _relocate_appended_before(doc, before=before, last_before=last)
        elif not n_img:
            _dash_after(doc, para, "id_legal", n)
            return f"截件有法定代表人身份证位置，未贴上{who}的扫描件"
        return f"已贴上{who}的身份证"
    if files:
        return "截件没有法定代表人身份证标题，未贴扫描件"
    return ""


def _slice_pdf_pages(pdf: Path, dest: Path, start: int, end: int) -> None:
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(pdf))
    n = len(reader.pages)
    a = max(0, start - 1)
    b = min(n, max(start, end))
    if a >= b:
        raise RuntimeError(f"格式章页码超出 PDF（共 {n} 页，要第 {start}-{end} 页）")
    writer = PdfWriter()
    for i in range(a, b):
        writer.add_page(reader.pages[i])
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as fh:
        writer.write(fh)


def _convert_pages(pdf: Path, dest: Path, start: int, end: int) -> None:
    """先落一份截出来的 PDF，再交给阿里云转 Word。"""
    from api.services.tenders.aliyun_doc import convert_pdf_to_docx

    sliced = dest.with_name(dest.name.replace(".format.docx.part", ".format.pdf"))
    if sliced == dest or not sliced.name.endswith(".format.pdf"):
        sliced = dest.with_suffix(".pdf")
    _slice_pdf_pages(pdf, sliced, start, end)
    convert_pdf_to_docx(sliced, dest)


def _format_source_mark(dest: Path) -> Path:
    return dest.with_name(dest.name + ".source")


def prepare_word_format_docx(src: Path, invitation_id: str) -> str:
    """从上传的 Word 里把投标文件格式章原样切出来，供预览和按本标生成。"""
    dest = format_docx_path(invitation_id)
    if dest is None or not src.is_file() or src.suffix.lower() != ".docx":
        return ""
    from api.services.tenders.slice_rules import SLICE_MARK

    mark = _format_source_mark(dest)
    if (
        dest.is_file()
        and dest.stat().st_size > 1000
        and mark.is_file()
        and mark.read_text(encoding="utf-8").strip() == SLICE_MARK
    ):
        return "已从 Word 原样截出投标文件格式章"
    try:
        _slice_word_format(src, dest)
    except Exception as exc:  # noqa: BLE001
        logger.warning("slice word format failed: %s", exc)
        dest.unlink(missing_ok=True)
        mark.unlink(missing_ok=True)
        return f"Word 格式章提取失败：{exc}"
    if not dest.is_file() or dest.stat().st_size < 1000:
        dest.unlink(missing_ok=True)
        mark.unlink(missing_ok=True)
        return "Word 里没有抽到投标文件格式章"
    mark.write_text(SLICE_MARK, encoding="utf-8")
    return "已从 Word 原样截出投标文件格式章，预览和按本标生成都用这份原版"


def _format_file_kind(dest: Path) -> str:
    mark = _format_source_mark(dest)
    try:
        return mark.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _usable_format(dest: Path) -> bool:
    try:
        return dest.is_file() and dest.stat().st_size > 1000
    except OSError:
        return False


def _word_format_file(invitation_id: str) -> Path | None:
    dest = format_docx_path(invitation_id)
    if dest is None:
        return None
    from api.services.tenders.slice_rules import SLICE_MARK

    kind = _format_file_kind(dest) if dest.is_file() else ""
    if kind == SLICE_MARK and _usable_format(dest):
        return dest
    word = dest.parent / f"{(invitation_id or '').strip()}.docx"
    if word.is_file():
        note = prepare_word_format_docx(word, invitation_id)
        if note and "失败" not in note and "没有抽到" not in note and _usable_format(dest):
            return dest
    return None


def assemble_from_word_format(
    brief,
    dest: Path,
    *,
    volume: str = "",
    catalog_slots=None,
    catalog_media=None,
    catalog_facts=None,
):
    """按本标：填格式章副本。技术标只裁正文，封面目录页码另写；只改标书写明的边距、正文字体和页码。"""
    if (getattr(brief, "layoutMode", None) or "").strip() != "outline":
        return None
    src = _word_format_file(getattr(brief, "invitationId", "") or "")
    if src is None:
        src = _ready_format_docx(getattr(brief, "invitationId", "") or "")
    if src is None:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    doc = Document(str(dest))
    kind = _format_file_kind(src)
    vol = (volume or "").strip().lower()
    _drop_invite_chrome(doc)
    slice_notes: list[str] = []
    tech_front = False
    if vol == "technical":
        kept = _keep_technical_only(doc)
        slice_notes.append(kept)
        tech_front = "只保留格式章里的技术部分" in kept
    elif vol == "business" and bool(getattr(brief, "splitSlice", False)):
        slice_notes.append(_drop_technical(doc))
    if kind == "pdf":
        _pull_pdf_blank_lines(doc)
    blank_notes = _fill_word_blanks(doc, brief, catalog_facts)
    sheet_notes = _fill_slice_sheets(doc, brief)
    blanks = _open_blank_labels(doc)
    lib_notes = _attach_library(doc, brief, catalog_media)
    pasted_ids = set()
    if any(("已贴上" in n or "未贴上" in n) and "身份证" in n for n in lib_notes):
        pasted_ids.add("id_legal")
    _scrub_undefined_toc(doc)
    _separate_toc_page(doc)
    fmt = getattr(brief, "documentFormat", None)
    if fmt is not None:
        from api.services.tenders.format_rules import apply_volume_format

        spec = fmt.technicalFormat if vol == "technical" else fmt.businessFormat
        slice_notes.extend(apply_volume_format(doc, spec))
    _restart_page_after_toc(doc)
    from api.services.tenders.placeholders import place_unmatched_slots

    slice_notes.extend(
        place_unmatched_slots(
            doc,
            slots=catalog_slots,
            media=catalog_media,
            required_keys=list(getattr(brief, "requiredSlotKeys", None) or []),
            volume=vol,
            skip_keys=pasted_ids,
            requirements=list(getattr(brief, "bidRequirements", None) or []),
        )
    )
    from api.services.tenders.document import _ensure_header

    anon = vol == "technical" and bool(getattr(brief, "techAnonymous", False))
    if tech_front:
        _install_tech_front(doc, brief, anon=anon)
    project = "" if anon else (getattr(brief, "projectName", "") or "").strip()
    for i, section in enumerate(doc.sections):
        _ensure_header(section, project, cover=(i == 0))
    # 仅新建的技术子集目录使用直接锚点；原格式章目录继续走原路径。
    linked = 0 if tech_front else _link_toc_body(doc)
    if not tech_front:
        linked += _repair_format_table_toc(doc, brief)
    if tech_front:
        _arm_tech_pages(doc, brief)
        from api.services.tenders.assemble import _fill_toc_pages
        from api.services.tenders.schema import DocumentFormat

        page_start = "body"
        if fmt is not None and fmt.technicalFormat.pageStartSet:
            page_start = fmt.technicalFormat.pageNumberStart
        _fill_toc_pages(doc, DocumentFormat(pageNumberStart=page_start), subset=True)
        toc_rows = [p for p in doc.paragraphs if "PAGEREF" in p._p.xml]
        linked = sum(p.text.rsplit("\t", 1)[-1].strip().isdigit() for p in toc_rows)
        pending = len(toc_rows) - linked
        if pending:
            slice_notes.append(f"子集目录 {pending} 条未能安全定位或估页，已保留「待更新」占位")
        slice_notes.append("技术标已另写封面和目录，页码从正文起编")
    doc.save(str(dest))
    if kind == "pdf":
        lead = "已按 PDF 转出的格式章组卷，预填横线收回同一行，正文表格未重排"
    else:
        lead = "已按上传的 Word 原版投标文件格式组卷，只填这份截件的副本"
    notes = [lead, *slice_notes, *blank_notes, *sheet_notes, *lib_notes]
    if blanks:
        shown = "、".join(blanks[:12])
        tail = f"等 {len(blanks)} 处" if len(blanks) > 12 else ""
        notes.append(f"对不上的空保持原样：{shown}{tail}")
    if linked:
        notes.append(f"目录 {linked} 条已按后文位置写上页码，从正文第 1 页起")
    elif not tech_front:
        notes.append("目录对不上页码的条目不写页码")
    return dest, notes


_TECH_HEAD = (
    "技术标",
    "技术方案",
    "技术偏离",
    "技术偏差",
    "施工组织",
    "技术规格",
    "暗标",
    "技术要求",
)
_BIZ_HEAD = (
    "投标函",
    "报价",
    "工程量清单",
    "授权委托",
    "身份证明",
    "营业执照",
    "商务偏离",
    "商务偏差",
    "业绩",
    "法定代表人",
)


def _section_kind(text: str) -> str:
    n = re.sub(r"\s+", "", text or "")
    if not n or len(n) > 40:
        return ""
    if n.startswith("目录") and len(n) <= 8:
        return "biz"
    if any(k in n for k in _TECH_HEAD):
        return "tech"
    if any(k in n for k in _BIZ_HEAD):
        return "biz"
    return ""


def _keep_technical_only(doc: Document) -> str:
    """技术标只留格式章里的技术部分。没有技术标题时整份不动。"""
    body = doc.element.body
    children = list(body)
    saw = False
    for el in children:
        if el.tag != qn("w:p"):
            continue
        if _section_kind(_el_text(el)) == "tech":
            saw = True
            break
    if not saw:
        return "格式章里没有单独的技术部分，技术标仍用整份截件"
    mode = ""
    for el in children:
        if el.tag == qn("w:sectPr"):
            continue
        if el.tag == qn("w:p"):
            kind = _section_kind(_el_text(el))
            if kind == "tech":
                mode = "tech"
            elif kind == "biz":
                mode = "biz"
        if mode == "tech":
            continue
        _detach_para_sect(el, body)
        body.remove(el)
    return "技术标只保留格式章里的技术部分"


def _drop_technical(doc: Document) -> str:
    """分卷商务标去掉格式章里的技术部分。没有技术标题时整份不动。"""
    body = doc.element.body
    children = list(body)
    saw = False
    for el in children:
        if el.tag != qn("w:p"):
            continue
        if _section_kind(_el_text(el)) == "tech":
            saw = True
            break
    if not saw:
        return "格式章里没有单独的技术部分，商务卷仍用整份截件"
    mode = ""
    for el in list(body):
        if el.tag == qn("w:sectPr"):
            continue
        if el.tag == qn("w:p"):
            kind = _section_kind(_el_text(el))
            if kind == "tech":
                mode = "tech"
            elif kind == "biz":
                mode = "biz"
        if mode != "tech":
            continue
        _detach_para_sect(el, body)
        body.remove(el)
    return "分卷商务标已去掉格式章里的技术部分"


_BARE_TECH = (
    "施工组织设计",
    "技术方案",
    "技术标",
    "技术偏离表",
    "技术偏差表",
    "施工组织",
)
_H_CHAP = re.compile(r"^第[一二三四五六七八九十百零〇0-9]+[章节]")
_H_CN = re.compile(r"^[一二三四五六七八九十]{1,3}、")
_H_PAREN = re.compile(r"^[（(][一二三四五六七八九十0-9]+[)）]")
_H_NUM = re.compile(r"^\d{1,2}[.．、]")


def _tech_head_level(text: str, el=None) -> int:
    n = re.sub(r"\s+", "", text or "")
    if not n or len(n) > 36 or "。" in n or n.endswith(("；", ";", "：", ":")):
        return 0
    if any(k in n for k in ("盖章", "签字", "我方", "投标人")):
        return 0
    styled = _outline_level(el) if el is not None else 0
    if styled:
        return styled
    if _H_CHAP.match(n) or _H_CN.match(n):
        return 1
    dotted = re.match(r"^(\d+(?:\.\d+)+)", n)
    if dotted:
        return 3 if dotted.group(1).count(".") >= 2 else 2
    if _H_PAREN.match(n) or _H_NUM.match(n):
        return 2
    if any(k in n for k in _BARE_TECH) and len(n) <= 18:
        return 1
    return 0


def _outline_level(el) -> int:
    ppr = el.find(qn("w:pPr"))
    if ppr is None:
        return 0
    outline = ppr.find(qn("w:outlineLvl"))
    if outline is not None:
        raw = outline.get(qn("w:val")) or ""
        if raw.isdigit():
            return min(3, int(raw) + 1)
    style = ppr.find(qn("w:pStyle"))
    if style is None:
        return 0
    val = style.get(qn("w:val")) or ""
    hit = re.search(r"(\d)", val)
    if hit and ("heading" in val.lower() or "标题" in val or val.isdigit()):
        return min(3, int(hit.group(1)) or 1)
    return 0


def _toc_entry_level(text: str) -> int:
    n = re.sub(r"\s+", "", text or "")
    n = re.sub(r"\d{1,3}$", "", n)
    return _tech_head_level(n) or 1


def _mark_outline(el, level: int) -> None:
    ppr = el.find(qn("w:pPr"))
    if ppr is None:
        ppr = OxmlElement("w:pPr")
        el.insert(0, ppr)
    for old in list(ppr.findall(qn("w:outlineLvl"))):
        ppr.remove(old)
    node = OxmlElement("w:outlineLvl")
    node.set(qn("w:val"), str(max(0, min(8, int(level) - 1))))
    ppr.append(node)


def _styled_p(text: str, *, size_pt: float, bold: bool = False, center: bool = False) -> object:
    p = OxmlElement("w:p")
    ppr = OxmlElement("w:pPr")
    p.append(ppr)
    if center:
        jc = OxmlElement("w:jc")
        jc.set(qn("w:val"), "center")
        ppr.append(jc)
    run = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    if bold:
        rpr.append(OxmlElement("w:b"))
        rpr.append(OxmlElement("w:bCs"))
    sz = OxmlElement("w:sz")
    sz.set(qn("w:val"), str(int(size_pt * 2)))
    rpr.append(sz)
    sz_cs = OxmlElement("w:szCs")
    sz_cs.set(qn("w:val"), str(int(size_pt * 2)))
    rpr.append(sz_cs)
    fonts = OxmlElement("w:rFonts")
    fonts.set(qn("w:ascii"), "宋体")
    fonts.set(qn("w:hAnsi"), "宋体")
    fonts.set(qn("w:eastAsia"), "宋体")
    rpr.append(fonts)
    run.append(rpr)
    node = OxmlElement("w:t")
    node.text = text
    run.append(node)
    p.append(run)
    return p


def _sect_break(doc: Document):
    p = OxmlElement("w:p")
    ppr = OxmlElement("w:pPr")
    sect = deepcopy(doc.sections[-1]._sectPr)
    for tag in ("w:headerReference", "w:footerReference", "w:pgNumType", "w:titlePg"):
        for old in list(sect.findall(qn(tag))):
            sect.remove(old)
    for old in list(sect.findall(qn("w:type"))):
        sect.remove(old)
    typ = OxmlElement("w:type")
    typ.set(qn("w:val"), "nextPage")
    sect.append(typ)
    ppr.append(sect)
    p.append(ppr)
    return p


def _collect_tech_heads(doc: Document) -> list[tuple]:
    found: list[tuple] = []
    for el in doc.element.body:
        if el.tag != qn("w:p"):
            continue
        raw = _el_text(el).strip()
        level = _tech_head_level(raw, el)
        if not level:
            continue
        title = re.sub(r"\s+", "", raw)[:36]
        if not title or title in {row[2] for row in found}:
            continue
        found.append((el, level, title))
        if len(found) >= 80:
            break
    return found


def _tech_cover_nodes(brief, *, anon: bool) -> list:
    nodes = [OxmlElement("w:p") for _ in range(4)]
    if not anon:
        project = (getattr(brief, "projectName", "") or "").strip()
        if project:
            nodes.append(_styled_p(project, size_pt=18, bold=True, center=True))
    nodes.append(_styled_p("技术标", size_pt=22, bold=True, center=True))
    if anon:
        return nodes
    bidder = (getattr(brief, "bidderName", "") or "").strip()
    if bidder:
        nodes.append(OxmlElement("w:p"))
        nodes.append(_styled_p(f"投标人：{bidder}", size_pt=14, center=True))
    return nodes


def _prepend_body(doc: Document, nodes: list) -> None:
    body = doc.element.body
    anchor = None
    for child in body:
        if child.tag != qn("w:sectPr"):
            anchor = child
            break
    for node in nodes:
        if anchor is not None:
            anchor.addprevious(node)
        else:
            tail = body.find(qn("w:sectPr"))
            if tail is not None:
                tail.addprevious(node)
            else:
                body.append(node)


def _collect_subset_tech_heads(doc: Document, brief) -> list[tuple]:
    """补齐标题在表格中的章节；补出的标题位于表前，重复收集不再补。"""
    from api.services.tenders.outline import compact_title, items_for_volume

    found = {el: (level, title, None) for el, level, title in _collect_tech_heads(doc)}
    selected = items_for_volume(brief, "technical")
    selected_keys = {(item.id, item.rawTitle or item.title) for item in selected}
    claimed_tables = set()
    for item in brief.outlineItems or []:
        if item.skipped:
            continue
        title = (item.rawTitle or item.title).strip()
        keys = _title_keys(title)
        paragraphs = [el for el in doc.element.body if el.tag == qn("w:p")]
        # 相同材料名可以同时是（五）子项和七、顶级项，先比完整编号。
        hit = next((el for el in paragraphs if _compact_el(el) == compact_title(title)), None)
        if hit is None:
            candidates = [el for el in paragraphs if _toc_heading_hit(_compact_el(el), keys)]
            if len(candidates) == 1:
                hit = candidates[0]
        if hit is None:
            headers = next((line for line in item.body.splitlines() if "|" in line), "")
            cells = [compact_title(cell) for cell in headers.split("|") if cell.strip()]
            tables = []
            for table in doc.element.body:
                if table.tag != qn("w:tbl") or table in claimed_tables:
                    continue
                title_in_table = any(
                    _toc_heading_hit(_compact_el(p), keys) for p in table.iter(qn("w:p"))
                )
                row = table.find(qn("w:tr"))
                row_text = _compact_el(row) if row is not None else ""
                header_hit = (
                    (item.id, item.rawTitle or item.title) in selected_keys
                    and len(cells) >= 2 and "".join(cells) == row_text
                )
                if title_in_table or header_hit:
                    tables.append(table)
            # 多张同表头时不能猜归属，以免错锚到另一节。
            if len(tables) == 1:
                table = tables[0]
                claimed_tables.add(table)
                hit = _styled_p(title, size_pt=14, bold=True, center=True)
                table.addprevious(hit)
        if hit is not None:
            found[hit] = (item.level, title, item)
    heads = [(el, *found[el]) for el in doc.element.body if el in found]
    # 卷总题不是章节编号；有实际章节时不将它混入同层编号计数。
    if len(heads) > 1 and heads[0][2] in {"施工组织设计", "技术方案", "技术标"}:
        heads = heads[1:]
    # 已选技术子项无法安全定位时仍保留目录占位，不伪造正文锚点或页码。
    for index, item in enumerate(selected):
        key = (item.id, item.rawTitle or item.title)
        if any(source is not None and (source.id, source.rawTitle or source.title) == key
               for _el, _level, _title, source in heads):
            continue
        later = {(entry.id, entry.rawTitle or entry.title) for entry in selected[index + 1:]}
        at = next((i for i, (_el, _level, _title, source) in enumerate(heads)
                   if source is not None and (source.id, source.rawTitle or source.title) in later),
                  len(heads))
        heads.insert(at, (None, item.level, item.rawTitle or item.title, item))
    return heads


def _install_tech_front(doc: Document, brief, *, anon: bool) -> None:
    """新建技术子集目录，直接关联已定位的正文标题，避开重新匹配编号。"""
    from docx.text.paragraph import Paragraph

    from api.services.tenders.assemble import _write_toc
    from api.services.tenders.document import _bookmark_paragraph, _subset_bookmarks
    from api.services.tenders.outline import normalize_subset_outline
    from api.services.tenders.schema import DocumentFormat, OutlineItem

    if any(
        el.get(qn("w:name")) == "weitai_subset_toc"
        for el in doc.element.iter(qn("w:bookmarkStart"))
    ):
        return
    heads = _collect_subset_tech_heads(doc, brief)
    if not heads:
        for el in doc.element.body:
            if el.tag != qn("w:p"):
                continue
            title = re.sub(r"\s+", "", _el_text(el))[:36]
            if title:
                heads = [(el, 1, title, None)]
                break
    items = normalize_subset_outline([
        source.model_copy(update={"rawTitle": title}) if source is not None else
        OutlineItem(id=f"subset_{i + 1:02d}", title=title, level=level)
        for i, (_el, level, title, source) in enumerate(heads)
    ])
    bookmarks = _subset_bookmarks(doc, items)
    for (el, _level, _title, _source), item, bookmark in zip(heads, items, bookmarks, strict=True):
        if el is None:
            continue
        _mark_outline(el, item.level)
        _bookmark_paragraph(Paragraph(el, doc), bookmark)
    old = set(doc.element.body)
    fmt = brief.documentFormat or DocumentFormat()
    _write_toc(doc, items, brief, fmt, bookmarks, subset=True)
    toc_nodes = [el for el in doc.element.body if el not in old]
    _bookmark_paragraph(Paragraph(toc_nodes[0], doc), "weitai_subset_toc")
    nodes = _tech_cover_nodes(brief, anon=anon)
    nodes.append(_sect_break(doc))
    nodes.extend(toc_nodes)
    nodes.append(_sect_break(doc))
    # 移动刚生成的目录段落，正文和原始格式文件都不重写。
    _prepend_body(doc, nodes)
    _wrap_toc_field(toc_nodes[1:])


def _arm_tech_pages(doc: Document, brief) -> None:
    """封面和目录不编页，正文节从 1 起，页脚放 PAGE 域。"""
    from api.services.tenders.format_rules import apply_footer_page_number, section_page_flags
    from api.services.tenders.schema import DocumentFormat

    spec = None
    fmt = getattr(brief, "documentFormat", None)
    if fmt is not None:
        spec = getattr(fmt, "technicalFormat", None)
    start = "body"
    pos = "bottom-center"
    font = "宋体"
    if spec is not None:
        if spec.pageStartSet and spec.pageNumberStart:
            start = spec.pageNumberStart
        if spec.pagePosSet and spec.pageNumberPos:
            pos = spec.pageNumberPos
        if spec.fontSet and spec.fontName:
            font = spec.fontName
    if pos == "none":
        return
    sections = list(doc.sections)
    if start == "cover":
        body_si = 0
    elif start == "toc":
        body_si = 1 if len(sections) > 1 else 0
    else:
        body_si = 2 if len(sections) >= 3 else 0
    tmp = DocumentFormat(pageNumberStart=start, pageNumberPos=pos, fontName=font)
    for i, section in enumerate(sections):
        numbered, restart = section_page_flags(i, tmp, body_section=body_si)
        apply_footer_page_number(section, tmp, numbered=numbered, restart=restart)


def scrub_anonymous_docx(path: Path, brief) -> None:
    """技术暗标：正文、目录、页眉页脚里的人名和公司名去掉，页眉页脚里的图也去掉，并清文档作者、公司。"""
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))
    names: list[str] = []
    for attr in ("bidderName", "legalPersonName", "agentName", "pmName"):
        text = str(getattr(brief, attr, "") or "").strip()
        if len(text) >= 2 and text not in names:
            names.append(text)
    if names:
        for node in doc.element.body.iter(qn("w:p")):
            _wash_para(Paragraph(node, doc), names)
        for section in doc.sections:
            for part in _header_footer_parts(section):
                for node in part._element.iter(qn("w:p")):
                    _wash_para(Paragraph(node, part), names)
    for section in doc.sections:
        for part in _header_footer_parts(section):
            _strip_drawings(part._element)
    _clear_doc_meta(doc)
    doc.save(str(path))


_HF_PART = {
    ("header", "default"): "header",
    ("header", "even"): "even_page_header",
    ("header", "first"): "first_page_header",
    ("footer", "default"): "footer",
    ("footer", "even"): "even_page_footer",
    ("footer", "first"): "first_page_footer",
}


def _header_footer_parts(section) -> list:
    """只打开节里已经挂上的页眉页脚，避免凭空造出偶数页页眉。"""
    parts = []
    seen: set[str] = set()
    for el in section._sectPr:
        if el.tag == qn("w:headerReference"):
            kind = "header"
        elif el.tag == qn("w:footerReference"):
            kind = "footer"
        else:
            continue
        typ = el.get(qn("w:type")) or "default"
        name = _HF_PART.get((kind, typ))
        if not name or name in seen:
            continue
        seen.add(name)
        part = getattr(section, name, None)
        if part is not None:
            parts.append(part)
    return parts


def _strip_drawings(element) -> None:
    for tag in ("w:drawing", "w:pict"):
        for node in list(element.iter(qn(tag))):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)


def _wash_para(para, names: list[str]) -> None:
    for run in para.runs:
        text = run.text or ""
        washed = text
        for name in names:
            if name in washed:
                washed = washed.replace(name, "")
        if washed != text:
            run.text = washed
    blob = para.text or ""
    if not any(name in blob for name in names):
        return
    washed = blob
    for name in names:
        washed = washed.replace(name, "")
    if para.runs:
        para.runs[0].text = washed
        for run in para.runs[1:]:
            run.text = ""


def _clear_doc_meta(doc: Document) -> None:
    props = doc.core_properties
    props.author = ""
    props.last_modified_by = ""
    from lxml import etree

    for rel in doc.part.package.rels.values():
        if "extended-properties" not in (rel.reltype or ""):
            continue
        part = rel.target_part
        root = getattr(part, "element", None)
        own = root is None
        if own:
            root = etree.fromstring(part.blob)
        changed = False
        for child in list(root):
            tag = child.tag.rsplit("}", 1)[-1]
            if tag in {"Company", "Manager"} and (child.text or ""):
                child.text = ""
                changed = True
        if own and changed:
            part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _detach_para_sect(el, body) -> None:
    if el.tag != qn("w:p"):
        return
    ppr = el.find(qn("w:pPr"))
    if ppr is None:
        return
    sect = ppr.find(qn("w:sectPr"))
    if sect is None:
        return
    ppr.remove(sect)
    if body.find(qn("w:sectPr")) is None:
        body.append(sect)


_RULE_SKIP = ("序号", "金额", "单价", "合价", "数量", "规格", "备注", "项目内容")
_RULE_TAIL = re.compile(r"^[（(][^）)]{1,14}[)）]$")


def _pull_pdf_blank_lines(doc: Document) -> None:
    """pdf 转档把预填横线画成小表。收回同一行，正文表不动。"""
    body = doc.element.body
    for tbl in list(body.findall(qn("w:tbl"))):
        if tbl.getparent() is not body:
            continue
        if not _is_blank_rule(tbl):
            continue
        _fold_blank_rule(tbl)


def _is_blank_rule(tbl) -> bool:
    rows = tbl.findall(qn("w:tr"))
    if not rows or len(rows) > 2:
        return False
    if len(rows) == 2 and any(_cell_plain(tc) for tc in rows[1].findall(qn("w:tc"))):
        return False
    for tr in rows:
        if _row_twips(tr) > 800:
            return False
    if not _has_drawn_border(tbl):
        return False
    bits = [_cell_plain(tc) for tc in rows[0].findall(qn("w:tc"))]
    if not bits or len(bits) > 8:
        return False
    kinds = [_bit_kind(bit) for bit in bits]
    if any(kind == "text" for kind in kinds):
        return False
    if not any(kind in {"blank", "label"} for kind in kinds):
        return False
    blob = "".join(bits)
    return not any(word in blob for word in _RULE_SKIP)


def _bit_kind(text: str) -> str:
    if not text or re.fullmatch(r"[_\s＿—\-]+", text):
        return "blank"
    if _RULE_TAIL.fullmatch(text):
        return "suffix"
    if text.endswith(("：", ":")) and len(text) <= 18:
        return "label"
    if len(text) <= 2:
        return "chip"
    return "text"


def _row_twips(tr) -> int:
    tr_pr = tr.find(qn("w:trPr"))
    if tr_pr is None:
        return 0
    th = tr_pr.find(qn("w:trHeight"))
    if th is None:
        return 0
    raw = th.get(qn("w:val")) or ""
    return int(raw) if raw.isdigit() else 0


def _has_drawn_border(tbl) -> bool:
    for node in tbl.iter(qn("w:tblBorders"), qn("w:tcBorders")):
        for edge in node:
            val = (edge.get(qn("w:val")) or "").lower()
            if val and val not in {"nil", "none", "0"}:
                return True
    return False


def _rule_pieces(bits: list[str]) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    buf = ""
    for bit in bits:
        kind = _bit_kind(bit)
        if kind == "chip":
            buf += bit
            continue
        if kind == "label":
            buf += bit
            tokens.append(("label", buf))
            buf = ""
            continue
        if buf:
            tokens.append(("label", buf))
            buf = ""
        if kind == "suffix":
            tokens.append(("text", bit))
        elif kind == "blank":
            tokens.append(("line", ""))
    if buf:
        tokens.append(("label", buf))
    pieces: list[tuple[str, str]] = []
    for i, (kind, text) in enumerate(tokens):
        if kind == "label":
            shown = text if text.endswith(("：", ":")) else text + "："
            pieces.append(("text", shown))
            nxt = tokens[i + 1][0] if i + 1 < len(tokens) else ""
            if nxt != "line":
                pieces.append(("line", ""))
            continue
        if kind == "line":
            if pieces and pieces[-1][0] == "line":
                continue
            pieces.append(("line", ""))
            continue
        pieces.append(("text", text))
    return pieces


def _fold_blank_rule(tbl) -> None:
    rows = tbl.findall(qn("w:tr"))
    bits = [_cell_plain(tc) for tc in rows[0].findall(qn("w:tc"))]
    pieces = _rule_pieces(bits)
    if not pieces:
        return
    prev = _neighbor_para(tbl, step=-1)
    nxt = _neighbor_para(tbl, step=1)
    pure = all(kind == "line" for kind, _text in pieces)
    host = prev if pure and _blank_host(prev) else None
    if host is None:
        host = OxmlElement("w:p")
        if prev is not None and prev.find(qn("w:pPr")) is not None:
            host.append(deepcopy(prev.find(qn("w:pPr"))))
        tbl.addprevious(host)
    tail = ""
    prev_text = _compact_el(host) if host is not None else ""
    if nxt is not None:
        raw = _el_text(nxt).strip()
        compact = re.sub(r"\s+", "", raw)
        if _RULE_TAIL.fullmatch(compact) or (
            not prev_text.endswith(("。", "；", "：", ":", "）", ")"))
            and compact.startswith("标段")
            and len(compact) <= 24
        ):
            tail = raw
    _write_rule_pieces(host, pieces, tail)
    if tail and nxt is not None:
        parent = nxt.getparent()
        if parent is not None:
            parent.remove(nxt)
    parent = tbl.getparent()
    if parent is not None:
        parent.remove(tbl)


def _blank_host(el) -> bool:
    if el is None:
        return False
    text = _compact_el(el)
    if not text or len(text) > 48 or text == "目录":
        return False
    return _HEADING_LINE.match(text) is None


def _neighbor_para(tbl, *, step: int):
    node = tbl.getprevious() if step < 0 else tbl.getnext()
    while node is not None:
        if node.tag == qn("w:p") and _compact_el(node):
            return node
        if node.tag == qn("w:tbl"):
            return None
        node = node.getprevious() if step < 0 else node.getnext()
    return None


def _write_rule_pieces(host, pieces: list[tuple[str, str]], tail: str) -> None:
    slots = sum(1 for kind, _text in pieces if kind == "line")
    text_vis = sum(_vis_width(text) for kind, text in pieces if kind == "text")
    text_vis += _vis_width(tail)
    room = 52 - text_vis
    per = room // max(slots, 1) // 2
    em = 4 if room < 8 else max(4, min(8, per))
    for kind, text in pieces:
        if kind == "line":
            _append_rule(host, em)
        elif text:
            _append_plain(host, text)
    if tail:
        _append_plain(host, tail)
    _no_wrap_para(host)


def _append_plain(host, text: str) -> None:
    run = OxmlElement("w:r")
    rpr = _sample_rpr(host, underline=False)
    if rpr is not None:
        run.append(rpr)
    node = OxmlElement("w:t")
    node.text = text
    if text[:1].isspace() or text[-1:].isspace():
        node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    run.append(node)
    host.append(run)


def _append_rule(host, em: int) -> None:
    run = OxmlElement("w:r")
    rpr = _sample_rpr(host, underline=True)
    run.append(rpr)
    node = OxmlElement("w:t")
    node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    node.text = "\u3000" * em
    run.append(node)
    host.append(run)


def _sample_rpr(host, *, underline: bool):
    sample = host.find(qn("w:r"))
    src = None if sample is None else sample.find(qn("w:rPr"))
    rpr = deepcopy(src) if src is not None else OxmlElement("w:rPr")
    for old in rpr.findall(qn("w:u")):
        rpr.remove(old)
    for old in rpr.findall(qn("w:b")):
        rpr.remove(old)
    if underline:
        u = OxmlElement("w:u")
        u.set(qn("w:val"), "single")
        rpr.append(u)
    elif src is None:
        return None
    return rpr


def _no_wrap_para(host) -> None:
    ppr = host.find(qn("w:pPr"))
    if ppr is None:
        ppr = OxmlElement("w:pPr")
        host.insert(0, ppr)
    for old in ppr.findall(qn("w:wordWrap")):
        ppr.remove(old)
    el = OxmlElement("w:wordWrap")
    el.set(qn("w:val"), "off")
    ppr.append(el)


def _source_kind(path: Path) -> str:
    mark = tender_invitations_dir() / f"{path.name}.source"
    if not mark.is_file():
        mark = path.with_name(path.name + ".source")
    try:
        return mark.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def tidy_format_file(path: Path) -> None:
    """预览用：只去掉格式章扉页，不改字体、目录和联系方式。"""
    doc = Document(str(path))
    _drop_invite_chrome(doc)
    doc.save(str(path))


def _drop_invite_chrome(doc: Document) -> None:
    """去掉「第X章、投标文件格式」扉页，以及「-185-」这种印刷页码。"""
    body = doc.element.body
    children = list(body)
    from api.services.tenders.slice_rules import format_content_start

    # 同时兼容已截取的旧缓存；与封面同页或分页证据不足时保持原样。
    for i, el in enumerate(children):
        if el.tag == qn("w:p") and not _el_text(el).strip() and not any(
            next(el.iter(qn("w:" + name)), None) is not None for name in ("drawing", "pict", "object")
        ):
            continue
        start = format_content_start(children, i, len(children))
        if start > i:
            for old in children[:start]:
                body.remove(old)
        break
    for el in list(body):
        if el.tag != qn("w:p"):
            continue
        compact = re.sub(r"\s+", "", _el_text(el))
        if compact and re.fullmatch(r"-?\d{1,4}-?", compact):
            body.remove(el)


def _fix_cover_leader_line(doc: Document) -> None:
    """「投标人或联合体牵头人」转档后常丢下划线，（盖单位章）被甩到下一行。"""
    from api.services.tenders.document import _form_run

    for para in list(doc.paragraphs):
        n = re.sub(r"\s+", "", para.text or "")
        if n != "投标人或联合体牵头人：":
            continue
        if any(_run_under(r) for r in para._p.iter(qn("w:r"))):
            continue
        _form_run(para, "\u3000" * 6, underline=True)
        nxt = para._p.getnext()
        if nxt is not None and nxt.tag == qn("w:p") and re.sub(r"\s+", "", _el_text(nxt)) == "（盖单位章）":
            _form_run(para, "（盖单位章）", underline=False)
            parent = nxt.getparent()
            if parent is not None:
                parent.remove(nxt)


def _vis_width(text: str) -> int:
    n = 0
    for ch in text or "":
        n += 1 if ord(ch) < 128 else 2
    return n


def _pad_label(label: str) -> str:
    """冒号对齐到「项目联系人电话（手机号）」那一列。"""
    gap = _vis_width("项目联系人电话（手机号）：") - _vis_width(label)
    if gap <= 0:
        return label
    return ("\u3000" * (gap // 2)) + (" " if gap % 2 else "") + label


def _centered_field(doc: Document, anchor, label: str, value: str, *, align=None) -> None:
    from docx.text.paragraph import Paragraph

    from api.services.tenders.document import _form_run

    new_p = OxmlElement("w:p")
    anchor.addprevious(new_p)
    para = Paragraph(new_p, doc)
    para.alignment = align if align is not None else WD_ALIGN_PARAGRAPH.RIGHT
    _form_run(para, label, underline=False)
    _form_run(para, value if value else "\u3000" * 8, underline=True)


def _fix_contact_lines(doc: Document, brief) -> None:
    """pdf 转 Word 把地址、电话拆成错位的表，邮政编码和邮箱又没有下划线。"""
    from docx.text.paragraph import Paragraph

    from api.services.tenders.document import _form_run, _ymd

    addr = (getattr(brief, "bidderAddress", "") or "").strip() if brief else ""
    site = (getattr(brief, "bidderWebsite", "") or "").strip() if brief else ""
    phone = (getattr(brief, "bidderPhone", "") or "").strip() if brief else ""
    fax = (getattr(brief, "bidderFax", "") or "").strip() if brief else ""
    post = (getattr(brief, "bidderPostcode", "") or "").strip() if brief else ""
    mail = (getattr(brief, "bidderEmail", "") or "").strip() if brief else ""
    for tbl in list(doc.element.body.findall(qn("w:tbl"))):
        bits = [_cell_plain(tc) for tr in tbl.findall(qn("w:tr")) for tc in tr.findall(qn("w:tc"))]
        blob = "".join(bits)
        if len(blob) > 24:
            continue
        if "地" in blob and "网" in blob and "址" in blob:
            _centered_field(doc, tbl, _pad_label("地\u3000\u3000址："), addr)
            _centered_field(doc, tbl, _pad_label("网\u3000\u3000址："), site)
            tbl.getparent().remove(tbl)
            continue
        rows = []
        for tr in tbl.findall(qn("w:tr")):
            rows.append("".join(_cell_plain(tc) for tc in tr.findall(qn("w:tc"))))
        joined = "".join(rows)
        if joined in {"电话：传真：", "电话:传真:"} or joined.replace("：", "") in {"电话传真"}:
            _centered_field(doc, tbl, _pad_label("电\u3000\u3000话："), phone)
            _centered_field(doc, tbl, _pad_label("传\u3000\u3000真："), fax)
            tbl.getparent().remove(tbl)
    for para in list(doc.paragraphs):
        n = re.sub(r"\s+", "", para.text or "")
        if "邮政编码" not in n or "邮箱" not in n or "年" not in n or len(n) > 48:
            continue
        anchor = para._p
        year = month = day = ""
        if brief is not None:
            year, month, day = _ymd(getattr(brief, "bidDate", "") or "")
        _centered_field(doc, anchor, _pad_label("邮政编码："), post)
        _centered_field(doc, anchor, _pad_label("项目联系人电话（手机号）："), phone)
        _centered_field(doc, anchor, _pad_label("邮\u3000\u3000箱："), mail)
        new_p = OxmlElement("w:p")
        anchor.addprevious(new_p)
        date_p = Paragraph(new_p, doc)
        date_p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        for part, lab in ((year, "年"), (month, "月"), (day, "日")):
            shown = part if (part or "").strip("　 ") else "\u3000\u3000"
            _form_run(date_p, shown, underline=True)
            _form_run(date_p, lab, underline=False)
        if "投标函附录" in n:
            app = OxmlElement("w:p")
            anchor.addprevious(app)
            app_p = Paragraph(app, doc)
            app_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _form_run(app_p, "（二）投标函附录", underline=False)
        parent = anchor.getparent()
        if parent is not None:
            parent.remove(anchor)


def _slice_word_format(src: Path, dest: Path) -> None:
    from api.services.tenders.slice_rules import cut_format_docx

    cut_format_docx(src, dest)


def _is_toc_heading(text: str) -> bool:
    n = re.sub(r"\s+", "", text or "")
    return n in {"目录", "目次"} or (n.startswith("目录") and len(n) <= 8)


def toc_block_ids(doc: Document) -> list:
    """目录标题到正文之前的段落。占位和贴图不能插进这段。

    返回元素本身。lxml 代理的 id() 会复用，不能拿 id 当键。
    """
    children = [el for el in doc.element.body if el.tag != qn("w:sectPr")]
    start = None
    for i, el in enumerate(children):
        if el.tag == qn("w:p") and _is_toc_heading(_el_text(el)):
            start = i
            break
    if start is None:
        return []
    els = [children[start]]
    lines: list[str] = []
    for el in children[start + 1 :]:
        if el.tag == qn("w:tbl"):
            break
        if el.tag != qn("w:p"):
            continue
        text = _compact_el(el)
        starts = _starts_new_page(el)
        if starts and lines:
            if text and len(text) <= 48 and "。" not in text and not _repeats_toc(text, lines):
                els.append(el)
            break
        if not text:
            els.append(el)
            continue
        if len(text) > 48 or "。" in text:
            break
        if len(lines) >= 2 and _repeats_toc(text, lines):
            break
        els.append(el)
        lines.append(text)
    return els


def _toc_page_index(pages: list[list]) -> int | None:
    for i, page in enumerate(pages):
        lines = [re.sub(r"\s+", "", _el_text(el)) for el in page]
        lines = [ln for ln in lines if ln]
        if not lines or lines[0] != "目录":
            continue
        short = [ln for ln in lines[1:] if 2 <= len(ln) <= 30 and not ln.endswith(("。", "；", ";"))]
        if len(short) >= 3:
            return i
    return None


def _title_keys(title: str) -> list[str]:
    key = re.sub(r"\s+", "", title or "")
    key = re.sub(r"^[一二三四五六七八九十百零〇0-9]+、", "", key)
    key = re.sub(r"^[（(][一二三四五六七八九十0-9]+[)）]", "", key)
    key = re.sub(r"^\d+(?:\.\d+)+", "", key).lstrip(".、")
    keys = [key]
    # 目录写「施工组织格式」，正文标题是「施工组织设计」
    if key.endswith("格式") and len(key) > 4:
        keys.append(key[:-2] + "设计")
        keys.append(key[:-2])
    return [k for k in keys if len(k) >= 2]


def _page_of_title(pages: list[list], title: str) -> int | None:
    """页首标题优先。函里「……身份证明或授权委托书；」这种清单句跳过。"""
    best: tuple[int, int] | None = None
    for key in _title_keys(title):
        for offset, page in enumerate(pages, start=1):
            blob = re.sub(r"\s+", "", _page_text(page))
            if not blob:
                continue
            at = blob.find(key)
            if at < 0:
                continue
            nxt = blob[at + len(key) : at + len(key) + 1]
            if nxt and nxt in "；;，,。或":
                continue
            if at == 0:
                return offset
            if best is None or at < best[0]:
                best = (at, offset)
    return None if best is None else best[1]


_TOC_PIECE = re.compile(
    r"[一二三四五六七八九十]{1,3}、(?:(?![一二三四五六七八九十]{1,3}、|[（(]).)*"
    r"|[（(][一二三四五六七八九十0-9]+[)）](?:(?![一二三四五六七八九十]{1,3}、|[（(]).)*"
)


def _set_para_plain(el, text: str, *, indent: bool = False, rpr=None) -> None:
    for child in list(el):
        if child.tag == qn("w:r"):
            el.remove(child)
    if indent:
        ppr = el.find(qn("w:pPr"))
        if ppr is None:
            ppr = OxmlElement("w:pPr")
            el.insert(0, ppr)
        ind = ppr.find(qn("w:ind"))
        if ind is None:
            ind = OxmlElement("w:ind")
            ppr.append(ind)
        ind.set(qn("w:left"), "840")
    run = OxmlElement("w:r")
    if rpr is not None:
        run.append(deepcopy(rpr))
    node = OxmlElement("w:t")
    node.text = text
    run.append(node)
    el.append(run)


def _explode_toc_para(el) -> None:
    raw = re.sub(r"\s+", "", _el_text(el))
    parts = [p for p in _TOC_PIECE.findall(raw) if p]
    if len(parts) < 2:
        return
    first = el.find(qn("w:r"))
    rpr = None
    if first is not None and first.find(qn("w:rPr")) is not None:
        rpr = deepcopy(first.find(qn("w:rPr")))
    _set_para_plain(el, parts[0], rpr=rpr)
    prev = el
    src_ppr = el.find(qn("w:pPr"))
    for piece in parts[1:]:
        new_p = OxmlElement("w:p")
        prev.addnext(new_p)
        if src_ppr is not None:
            new_p.insert(0, deepcopy(src_ppr))
        _set_para_plain(new_p, piece, indent=piece.startswith(("（", "(")), rpr=rpr)
        prev = new_p


def _norm_toc(text: str) -> str:
    return (
        text.replace("（", "(")
        .replace("）", ")")
        .replace("“", '"')
        .replace("”", '"')
    )


def _toc_heading_hit(text: str, keys: list[str]) -> bool:
    if not text or len(text) > 36 or "。" in text or text.endswith(("；", ";")):
        return False
    bare = _norm_toc(_strip_toc_no(text))
    text = _norm_toc(text)
    for key in sorted((_norm_toc(k) for k in keys), key=len, reverse=True):
        if len(key) < 4 and key not in {text, bare}:
            continue
        if text == key or bare == key:
            return True
        if len(key) >= 4 and (text.endswith(key) or bare.endswith(key)) and len(text) <= len(key) + 8:
            return True
    return False


def _format_toc_targets(doc: Document, brief) -> list[tuple]:
    """按正文位置及原书签编号恢复旧格式章目录的层级依据。"""
    from docx.text.paragraph import Paragraph

    from api.services.tenders.document import _pageref_bookmark
    from api.services.tenders.format_rules import peel_toc_lead
    from api.services.tenders.outline import compact_title
    from api.services.tenders.schema import OutlineItem

    block = toc_block_ids(doc)
    if len(block) < 2:
        return []
    body = list(doc.element.body)
    remaining = body[body.index(block[-1]) + 1:]
    targets = []
    for index, entry in enumerate(block[1:]):
        if entry.tag != qn("w:p"):
            continue
        para = Paragraph(entry, doc)
        title = re.sub(r"[.．·…]{3,}\s*$", "", para.text.split("\t", 1)[0]).strip()
        if not title or len(title) > 40 or _is_toc_heading(title):
            continue
        keys = _title_keys(title)
        hits = []
        for el in remaining:
            if el.tag not in {qn("w:p"), qn("w:tbl")}:
                continue
            direct = Paragraph(el, doc).text if el.tag == qn("w:p") else ""
            if _toc_heading_hit(re.sub(r"\s+", "", direct), keys) or any(
                _toc_heading_hit(_compact_el(p), keys) for p in el.iter(qn("w:p"))
            ):
                hits.append(el)
        if len(hits) != 1:
            continue
        hit = hits[0]
        names = [node.get(qn("w:name"), "") for node in hit.iter(qn("w:bookmarkStart"))]
        numbered = [name for name in names if not name.startswith(("_", "toc_"))
                    and compact_title(peel_toc_lead(name)) == compact_title(peel_toc_lead(title))]
        source_title = numbered[0] if len(numbered) == 1 else ""
        source = next((item for item in brief.outlineItems if not item.skipped
                       and compact_title(item.title) == compact_title(peel_toc_lead(title))), None)
        # 本地 ID 只作为分配器输入；PAGEREF 和锚点共享其返回的名字。
        item = OutlineItem(id=source.id if source is not None else f"format_{index:02d}", title=title,
                           level=source.level if source is not None else _toc_entry_level(title))
        targets.append((entry, hit, item, source_title, _pageref_bookmark(para)))
    return targets


def _repair_format_table_toc(doc: Document, brief) -> int:
    """补漏项引用，依原编号统一目录缩进，不重编目录或改正文样式。"""
    from docx.text.paragraph import Paragraph

    from api.services.tenders.assemble import _anchor_section
    from api.services.tenders.document import (
        _add_pageref_run, _apply_toc_indent, _estimate_bookmark_pages, _subset_bookmarks,
        _set_dot_tab,
    )
    from api.services.tenders.outline import normalize_subset_outline
    from api.services.tenders.schema import DocumentFormat

    targets = _format_toc_targets(doc, brief)
    if not targets:
        return 0
    sources = {item.id: title for _entry, _hit, item, title, _bm in targets if title}
    normalized = normalize_subset_outline([row[2] for row in targets], source_titles=sources)
    # 保留原目录已有的页码右边界；所有行清掉额外左制表位，使用同一套点线。
    right_pos = next((int(tab.get(qn("w:pos")))
                      for entry, *_ in targets for tab in entry.iter(qn("w:tab"))
                      if tab.get(qn("w:val")) == "right" and tab.get(qn("w:pos"))),
                     int(_toc_tab_pos(doc)))
    # 无原始编号的旧范本保持原样；不能仅凭裸标题猜父子关系。
    for (entry, _hit, item, title, _bm), norm in zip(targets, normalized, strict=True):
        if title:
            para = Paragraph(entry, doc)
            _apply_toc_indent(para, norm.level, preserve_spacing=True)
            # 原自动编号继承的尾随 Tab 会吃掉页码制表位。仅将原编号原样
            # 固化在目录行，不重新计数，也不修改正文的编号定义。
            num = entry.pPr.find(qn("w:numPr"))
            if num is not None and title.endswith(item.title):
                prefix = title[:-len(item.title)]
                if prefix:
                    run = OxmlElement("w:r")
                    text = OxmlElement("w:t")
                    text.text = prefix
                    run.append(text)
                    first_run = entry.find(qn("w:r"))
                    if first_run is not None:
                        first_run.addprevious(run)
                    else:
                        entry.append(run)
                    entry.pPr.remove(num)
    for entry, *_ in targets:
        _set_dot_tab(Paragraph(entry, doc), right_pos / 1440 * 2.54, normalize_runs=True)
    missing = [row for row in targets if not row[4]
               and (row[1].tag == qn("w:tbl")
                    or next(row[1].iter(qn("w:tbl")), None) is not None)]
    bookmarks = _subset_bookmarks(doc, [row[2] for row in missing])
    for (_entry, hit, item, _title, _bm), bookmark in zip(missing, bookmarks, strict=True):
        before = [el for el in doc.element.body if el.tag != qn("w:sectPr")].index(hit)
        _anchor_section(doc, before, item.title, bookmark,
                        brief.documentFormat or DocumentFormat(), subset=True)
    if not missing:
        return 0
    pages = _estimate_bookmark_pages(doc)
    first = next((pages[bm] for _e, _h, _i, _t, bm in targets if bm in pages), None)
    if first is None:
        return 0
    written = 0
    for (entry, _hit, _item, _title, _bm), bookmark in zip(missing, bookmarks, strict=True):
        if bookmark not in pages:
            continue
        para = Paragraph(entry, doc)
        if not any(node.getparent().tag == qn("w:r") for node in entry.iter(qn("w:tab"))):
            para.add_run().add_tab()
        _add_pageref_run(para, bookmark, str(max(1, pages[bookmark] - first + 1)))
        _set_dot_tab(para, right_pos / 1440 * 2.54, normalize_runs=True)
        written += 1
    return written


def _link_toc_body(doc: Document) -> int:
    """目录条目按后文标题所在页写页码。正文第一处从 1 起，封面和目录不占号。"""
    block = toc_block_ids(doc)
    if len(block) < 2:
        return 0
    entries = []
    for el in block[1:]:
        if el.tag != qn("w:p"):
            continue
        raw = _compact_el(el)
        if not raw or _is_toc_heading(raw) or len(raw) > 40 or "。" in raw:
            continue
        entries.append(el)
    if not entries:
        return 0
    cursor = block[-1].getnext()
    pairs: list[tuple] = []
    for ent in entries:
        keys = _title_keys(_compact_el(ent))
        found = None
        el = cursor
        while el is not None:
            if el.tag == qn("w:p") and _toc_heading_hit(_compact_el(el), keys):
                found = el
                break
            el = el.getnext()
        if found is None:
            continue
        pairs.append((ent, found))
        cursor = found.getnext()
    if not pairs:
        return 0
    from docx.text.paragraph import Paragraph

    from api.services.tenders.document import _bookmark_paragraph, _estimate_bookmark_pages

    for i, (toc_el, body_el) in enumerate(pairs):
        _bookmark_paragraph(Paragraph(body_el, doc), f"toc_fmt_{i:02d}")
        _mark_outline(body_el, _toc_entry_level(_compact_el(toc_el)))
    pages = _estimate_bookmark_pages(doc)
    first = pages.get("toc_fmt_00") or 0
    if first < 1:
        return 0
    tab = _toc_tab_pos(doc)
    written: list = []
    for i, (toc_el, _body_el) in enumerate(pairs):
        abs_page = pages.get(f"toc_fmt_{i:02d}") or 0
        if abs_page < 1:
            continue
        _set_toc_field(doc, toc_el, f"toc_fmt_{i:02d}", str(abs_page - first + 1), tab)
        written.append(toc_el)
    if written:
        _wrap_toc_field(written)
    return len(written)


def _stamp_toc_pages(doc: Document) -> int:
    children = [el for el in doc.element.body if el.tag != qn("w:sectPr")]
    pages = _split_pages(children)
    toc_i = _toc_page_index(pages)
    if toc_i is None:
        return 0
    for el in list(pages[toc_i]):
        if el.tag == qn("w:p"):
            _explode_toc_para(el)
    children = [el for el in doc.element.body if el.tag != qn("w:sectPr")]
    pages = _split_pages(children)
    toc_i = _toc_page_index(pages)
    if toc_i is None:
        return 0
    body_pages = pages[toc_i + 1 :]
    n = 0
    for el in pages[toc_i]:
        if el.tag != qn("w:p"):
            continue
        raw = _el_text(el).strip()
        key = re.sub(r"\s+", "", raw)
        if not key or key == "目录" or len(key) > 36:
            continue
        page_no = _page_of_title(body_pages, key)
        if page_no is None:
            continue
        _set_plain_page(el, str(page_no), _toc_tab_pos(doc))
        n += 1
    return n


def _toc_tab_pos(doc: Document) -> str:
    sect = doc.sections[-1] if doc.sections else None
    if sect is None or not sect.page_width:
        return "8500"
    pos = int(sect.page_width) - int(sect.left_margin or 0) - int(sect.right_margin or 0)
    if pos < 6000 or pos > 12000:
        return "8500"
    return str(pos)


def _set_plain_page(el, num: str, pos: str = "8500") -> None:
    for child in list(el):
        if child.tag != qn("w:r"):
            continue
        instr = "".join(t.text or "" for t in child.iter(qn("w:instrText")))
        if "PAGEREF" in instr or "HYPERLINK" in instr or child.find(qn("w:fldChar")) is not None:
            el.remove(child)
    text = _el_text(el).rstrip()
    if re.search(r"(?:\t| {2})\d{1,3}$", text):
        return
    ppr = el.find(qn("w:pPr"))
    if ppr is None:
        ppr = OxmlElement("w:pPr")
        el.insert(0, ppr)
    jc = ppr.find(qn("w:jc"))
    if jc is None:
        jc = OxmlElement("w:jc")
        ppr.append(jc)
    jc.set(qn("w:val"), "left")
    for old in ppr.findall(qn("w:tabs")):
        ppr.remove(old)
    tabs = OxmlElement("w:tabs")
    tab = OxmlElement("w:tab")
    tab.set(qn("w:val"), "right")
    tab.set(qn("w:leader"), "dot")
    tab.set(qn("w:pos"), pos)
    tabs.append(tab)
    ppr.append(tabs)
    sample = el.find(qn("w:r"))
    rpr = sample.find(qn("w:rPr")) if sample is not None else None
    run = OxmlElement("w:r")
    if rpr is not None:
        copied = deepcopy(rpr)
        for u in copied.findall(qn("w:u")):
            copied.remove(u)
        run.append(copied)
    run.append(OxmlElement("w:tab"))
    node = OxmlElement("w:t")
    node.text = str(num)
    run.append(node)
    el.append(run)


def _strip_toc_page_tail(el) -> None:
    _drop_pageref_fields(el)
    runs = [c for c in list(el) if c.tag == qn("w:r")]
    while runs:
        text = "".join(t.text or "" for t in runs[-1].iter(qn("w:t")))
        has_tab = runs[-1].find(qn("w:tab")) is not None
        if has_tab or re.fullmatch(r"\d{1,4}", (text or "").strip()):
            parent = runs[-1].getparent()
            if parent is not None:
                parent.remove(runs[-1])
            runs.pop()
            continue
        break


def _set_toc_field(doc: Document, el, bookmark: str, num: str, pos: str) -> None:
    """目录行：层级缩进、点线制表、PAGEREF。页码写在域结果里，Word 更新域后会重算。"""
    from docx.text.paragraph import Paragraph

    from api.services.tenders.document import _add_pageref_run, _no_underline, _set_dot_leader_font

    _strip_toc_page_tail(el)
    level = _outline_level(el) or _toc_entry_level(_el_text(el))
    ppr = el.find(qn("w:pPr"))
    if ppr is None:
        ppr = OxmlElement("w:pPr")
        el.insert(0, ppr)
    jc = ppr.find(qn("w:jc"))
    if jc is None:
        jc = OxmlElement("w:jc")
        ppr.append(jc)
    jc.set(qn("w:val"), "left")
    ind = ppr.find(qn("w:ind"))
    if ind is None:
        ind = OxmlElement("w:ind")
        ppr.append(ind)
    ind.set(qn("w:left"), str(420 * max(0, level - 1)))
    ind.set(qn("w:firstLine"), "0")
    for old in ppr.findall(qn("w:tabs")):
        ppr.remove(old)
    tabs = OxmlElement("w:tabs")
    tab = OxmlElement("w:tab")
    tab.set(qn("w:val"), "right")
    tab.set(qn("w:leader"), "dot")
    tab.set(qn("w:pos"), pos)
    tabs.append(tab)
    ppr.append(tabs)
    para = Paragraph(el, doc)
    tab_run = para.add_run()
    _set_dot_leader_font(tab_run)
    _no_underline(tab_run)
    tab_run.add_tab()
    _add_pageref_run(para, bookmark, num)


def _wrap_toc_field(entries: list) -> None:
    """目录标题上挂 TOC 域，条目留在域结果里。不另插空段，避免把原目录行挤开。"""
    if not entries:
        return
    host = entries[0].getprevious()
    while host is not None and host.tag == qn("w:p") and not _compact_el(host):
        host = host.getprevious()
    if host is None or host.tag != qn("w:p"):
        host = entries[0]
    blob = "".join(t.text or "" for t in host.iter(qn("w:instrText")))
    if "TOC" in blob:
        return

    def put(parent, node) -> None:
        run = OxmlElement("w:r")
        run.append(node)
        parent.append(run)

    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    put(host, begin)
    instr = OxmlElement("w:instrText")
    instr.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    instr.text = r' TOC \o "1-3" \h \z \u '
    put(host, instr)
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    put(host, sep)
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    put(entries[-1], end)


_UNDEF_MARK = re.compile(r"错误！未定义书签|错误!未定义书签")


def _drop_pageref_fields(el) -> None:
    """只删 PAGEREF 这一层域。外层超链接里的标题留下来。"""
    while True:
        runs = [c for c in list(el) if c.tag == qn("w:r")]
        removed = False
        i = 0
        while i < len(runs):
            kinds = [(f.get(qn("w:fldCharType")) or "") for f in runs[i].iter(qn("w:fldChar"))]
            if "begin" not in kinds:
                i += 1
                continue
            depth = 0
            own = ""
            separated = False
            buf: list = []
            j = i
            while j < len(runs):
                buf.append(runs[j])
                step = [(f.get(qn("w:fldCharType")) or "") for f in runs[j].iter(qn("w:fldChar"))]
                instr = "".join(t.text or "" for t in runs[j].iter(qn("w:instrText")))
                if "begin" in step:
                    depth += 1
                if depth == 1 and not separated:
                    own += instr
                if "separate" in step and depth == 1:
                    separated = True
                if "end" in step:
                    depth -= 1
                    if depth <= 0:
                        break
                j += 1
            if "PAGEREF" in own:
                for run in buf:
                    parent = run.getparent()
                    if parent is not None:
                        parent.remove(run)
                removed = True
                break
            i += 1
        if not removed:
            return


def _scrub_undefined_toc(doc: Document) -> None:
    """目录域对不上书签时去掉「错误！未定义书签」。对得上正文才写页码。"""
    children = [el for el in doc.element.body if el.tag != qn("w:sectPr")]
    pages = _split_pages(children)
    toc_i = _toc_page_index(pages)
    toc_ids = {id(el) for el in pages[toc_i]} if toc_i is not None else set()
    body_pages = pages[toc_i + 1 :] if toc_i is not None else []
    tab = _toc_tab_pos(doc)
    for el in children:
        if el.tag != qn("w:p"):
            continue
        raw = _el_text(el)
        instrs = "".join(t.text or "" for t in el.iter(qn("w:instrText")))
        if "PAGEREF" not in instrs and "未定义书签" not in raw:
            continue
        _drop_pageref_fields(el)
        for node in el.iter(qn("w:t")):
            if node.text and "未定义书签" in node.text:
                node.text = _UNDEF_MARK.sub("", node.text)
        if id(el) not in toc_ids:
            continue
        title = re.sub(r"\s+", "", _el_text(el))
        title = re.sub(r"\d{1,3}$", "", title)
        if not title or title == "目录" or len(title) > 40:
            continue
        page_no = _page_of_title(body_pages, title)
        if page_no is None:
            continue
        if re.search(r"(?:\t| {2})\d{1,3}$", _el_text(el)):
            continue
        _set_plain_page(el, str(page_no), tab)


_TOC_ENTRY = re.compile(
    r"^(?:[一二三四五六七八九十]+、|[（(][一二三四五六七八九十0-9]+[)）]|\d+[.、])"
)


def _compact_el(el) -> str:
    return re.sub(r"\s+", "", _el_text(el))


def _starts_new_page(el) -> bool:
    if el.tag != qn("w:p"):
        return False
    if _page_sect(el) is not None:
        return True
    for node in el.iter(qn("w:br")):
        if (node.get(qn("w:type")) or "") == "page":
            return True
    ppr = el.find(qn("w:pPr"))
    return ppr is not None and ppr.find(qn("w:pageBreakBefore")) is not None


def _separate_toc_page(doc: Document) -> None:
    """目录和后文还粘在同一页时，在目录后面补一个分页。已经换页的不动。"""
    children = [el for el in doc.element.body if el.tag != qn("w:sectPr")]
    start = None
    for i, el in enumerate(children):
        if el.tag == qn("w:p") and _compact_el(el) == "目录":
            start = i
            break
    if start is None:
        return
    toc_lines: list[str] = []
    boundary = None
    for el in children[start + 1 :]:
        if _starts_new_page(el):
            return
        if el.tag == qn("w:tbl"):
            boundary = el
            break
        if el.tag != qn("w:p"):
            continue
        text = _compact_el(el)
        if not text:
            continue
        if len(text) > 40 or "。" in text:
            prev = _prev_text_para(el)
            prev_text = _compact_el(prev) if prev is not None else ""
            earlier = toc_lines[:-1]
            if (
                prev is not None
                and prev_text
                and toc_lines
                and prev_text == toc_lines[-1]
                and (_repeats_toc(prev_text, earlier) or (_TOC_ENTRY.match(prev_text) is None and len(earlier) >= 2))
            ):
                boundary = prev
            else:
                boundary = el
            break
        if len(toc_lines) >= 2 and _repeats_toc(text, toc_lines) and (
            _TOC_ENTRY.match(text) is None or _next_is_body(el)
        ):
            boundary = el
            break
        toc_lines.append(text)
    if boundary is None or len(toc_lines) < 2:
        return
    prev = boundary.getprevious()
    if prev is not None and _starts_new_page(prev):
        return
    br_p = OxmlElement("w:p")
    run = OxmlElement("w:r")
    br = OxmlElement("w:br")
    br.set(qn("w:type"), "page")
    run.append(br)
    br_p.append(run)
    boundary.addprevious(br_p)


def _strip_toc_no(text: str) -> str:
    text = re.sub(r"^[一二三四五六七八九十0-9]+、", "", text)
    text = re.sub(r"^[（(][一二三四五六七八九十0-9]+[)）]", "", text)
    return re.sub(r"^\d+(?:\.\d+)+", "", text).lstrip(".、")


def _repeats_toc(text: str, toc_lines: list[str]) -> bool:
    own = _strip_toc_no(text)
    for prev in toc_lines:
        core = _strip_toc_no(prev)
        if text == prev or text == core or own == core or (len(text) >= 2 and text in core):
            return True
    return False


def _prev_text_para(el):
    prev = el.getprevious()
    while prev is not None and prev.tag == qn("w:p") and not _compact_el(prev):
        prev = prev.getprevious()
    if prev is not None and prev.tag == qn("w:p"):
        return prev
    return None


def _next_is_body(el) -> bool:
    nxt = el.getnext()
    while nxt is not None:
        if nxt.tag == qn("w:tbl"):
            return True
        if nxt.tag == qn("w:p"):
            text = _compact_el(nxt)
            if text and (len(text) > 40 or "。" in text):
                return True
            if text:
                return False
        nxt = nxt.getnext()
    return False


_HINTS = ("（项目名称）", "(项目名称)", "（招标人名称）", "（投标人名称）", "（采购人名称）")


_NOTE_LEAD = re.compile(r"^(备注|注)[：:]")


def _settle_table_note(tbl) -> None:
    """表下注释若被拆进最后一行，挪回表格下面。列宽不动。"""
    _unpin_note(tbl)
    _relax_exact_rows(tbl)
    rows = tbl.findall(qn("w:tr"))
    if len(rows) < 2:
        return
    last = rows[-1]
    parts: list[str] = []
    for tc in last.findall(qn("w:tc")):
        text = _cell_plain(tc)
        if text and (not parts or text != parts[-1]):
            parts.append(text)
    if not parts:
        return
    blob = "".join(parts)
    if not _NOTE_LEAD.match(blob) or len(blob) < 12:
        return
    for part in parts[1:]:
        if len(part) <= 6 and not part.startswith(("备注", "注")):
            return
    # 备注还在最后一行时，下一段对不上，浮动得先摘掉再挪
    _drop_tbl_float(tbl)
    nxt = _next_text_el(tbl)
    if nxt is not None and _compact_el(nxt).startswith(blob[:8]):
        tbl.remove(last)
        return
    new_p = OxmlElement("w:p")
    run = OxmlElement("w:r")
    first = last.find(qn("w:r"))
    rpr = first.find(qn("w:rPr")) if first is not None else None
    if rpr is not None:
        run.append(deepcopy(rpr))
    node = OxmlElement("w:t")
    node.text = blob
    run.append(node)
    new_p.append(run)
    tbl.addnext(new_p)
    tbl.remove(last)


def _next_text_el(el):
    nxt = el.getnext()
    while nxt is not None and nxt.tag == qn("w:p") and not _compact_el(nxt):
        nxt = nxt.getnext()
    if nxt is not None and nxt.tag == qn("w:p"):
        return nxt
    return None


def _drop_tbl_float(tbl) -> None:
    pr = tbl.find(qn("w:tblPr"))
    if pr is None:
        return
    for old in pr.findall(qn("w:tblpPr")):
        pr.remove(old)


def _unpin_note(tbl) -> None:
    """注释段紧挨着表时，去掉把字拉进表格的浮动和负间距。"""
    nxt = _next_text_el(tbl)
    if nxt is None or not _NOTE_LEAD.match(_compact_el(nxt)):
        return
    _drop_tbl_float(tbl)
    ppr = nxt.find(qn("w:pPr"))
    if ppr is None:
        return
    spacing = ppr.find(qn("w:spacing"))
    if spacing is None:
        return
    before = spacing.get(qn("w:before")) or ""
    if before.lstrip("-").isdigit() and int(before) < 0:
        spacing.set(qn("w:before"), "0")


def _relax_exact_rows(tbl) -> None:
    """固定行高压不住字时改成至少。列宽不动。纵向合并不算，高度是多行加起来的。"""
    cols = _grid_widths(tbl)
    for tr in tbl.findall(qn("w:tr")):
        tr_pr = tr.find(qn("w:trPr"))
        if tr_pr is None:
            continue
        th = tr_pr.find(qn("w:trHeight"))
        if th is None or (th.get(qn("w:hRule")) or "") != "exact":
            continue
        raw = th.get(qn("w:val")) or ""
        if not raw.isdigit() or int(raw) <= 0:
            continue
        if _row_overflows(tr, int(raw), cols):
            th.set(qn("w:hRule"), "atLeast")


def _grid_widths(tbl) -> list[int]:
    grid = tbl.find(qn("w:tblGrid"))
    if grid is None:
        return []
    out: list[int] = []
    for col in grid.findall(qn("w:gridCol")):
        raw = col.get(qn("w:w")) or ""
        out.append(int(raw) if raw.isdigit() else 0)
    return out


def _grid_span(tc) -> int:
    tc_pr = tc.find(qn("w:tcPr"))
    if tc_pr is None:
        return 1
    node = tc_pr.find(qn("w:gridSpan"))
    if node is None:
        return 1
    raw = node.get(qn("w:val")) or "1"
    n = int(raw) if raw.isdigit() else 1
    return n if n > 0 else 1


def _tc_width(tc) -> int:
    tc_pr = tc.find(qn("w:tcPr"))
    if tc_pr is None:
        return 0
    node = tc_pr.find(qn("w:tcW"))
    if node is None or (node.get(qn("w:type")) or "dxa") not in {"dxa", ""}:
        return 0
    raw = node.get(qn("w:w")) or ""
    return int(raw) if raw.isdigit() else 0


def _vmerge(tc) -> bool:
    tc_pr = tc.find(qn("w:tcPr"))
    return tc_pr is not None and tc_pr.find(qn("w:vMerge")) is not None


def _row_overflows(tr, height: int, cols: list[int]) -> bool:
    before = tr.find(qn("w:trPr"))
    skip = 0
    if before is not None:
        node = before.find(qn("w:gridBefore"))
        if node is not None and (node.get(qn("w:val")) or "").isdigit():
            skip = int(node.get(qn("w:val")) or 0)
    col_i = skip
    for tc in tr.findall(qn("w:tc")):
        span = _grid_span(tc)
        width = 0
        if cols and col_i < len(cols):
            width = sum(cols[col_i : col_i + span])
        tc_w = _tc_width(tc)
        cands = [n for n in (width, tc_w) if n > 200]
        if cands:
            width = min(cands)
        col_i += span
        if _vmerge(tc):
            continue
        if _cell_need(tc, width) > height + 80:
            return True
    return False


def _cell_need(tc, width: int) -> int:
    total = 0
    tc_pr = tc.find(qn("w:tcPr"))
    mar = None if tc_pr is None else tc_pr.find(qn("w:tcMar"))
    if mar is not None:
        for tag in ("top", "bottom"):
            node = mar.find(qn(f"w:{tag}"))
            raw = "" if node is None else (node.get(qn("w:w")) or "")
            if raw.isdigit():
                total += int(raw)
    lr = 216
    if mar is not None:
        lr = 0
        for tag in ("left", "right"):
            node = mar.find(qn(f"w:{tag}"))
            raw = "" if node is None else (node.get(qn("w:w")) or "")
            lr += int(raw) if raw.isdigit() else 108
    inner = width - lr if width > lr + 200 else 0
    for p in tc.findall(qn("w:p")):
        text = "".join(t.text or "" for t in p.iter(qn("w:t")))
        vis = _vis_width(text)
        if vis <= 0:
            continue
        font = _para_font_twips(p)
        line = _para_line_twips(p, font)
        if inner > 0:
            per = max(2, int(inner / (font / 2)))
            lines = max(1, (vis + per - 1) // per)
        else:
            lines = 2 if vis >= 36 else 1
        brs = 0
        for br in p.iter(qn("w:br")):
            if (br.get(qn("w:type")) or "") in {"", "textWrapping"}:
                brs += 1
        total += (lines + brs) * line
        ppr = p.find(qn("w:pPr"))
        sp = None if ppr is None else ppr.find(qn("w:spacing"))
        if sp is None:
            continue
        for key in ("before", "after"):
            raw = sp.get(qn(f"w:{key}")) or ""
            if raw.isdigit():
                total += int(raw)
    return total


def _para_font_twips(p) -> float:
    half = 21
    for rpr in p.iter(qn("w:rPr")):
        sz = rpr.find(qn("w:sz"))
        raw = "" if sz is None else (sz.get(qn("w:val")) or "")
        if raw.isdigit():
            half = max(half, int(raw))
    return half / 2 * 20


def _para_line_twips(p, font: float) -> int:
    ppr = p.find(qn("w:pPr"))
    sp = None if ppr is None else ppr.find(qn("w:spacing"))
    if sp is not None:
        raw = sp.get(qn("w:line")) or ""
        rule = sp.get(qn("w:lineRule")) or "auto"
        if raw.isdigit() and int(raw) > 0:
            n = int(raw)
            if rule == "exact":
                return n
            if rule == "atLeast":
                return max(n, int(font))
            return max(int(font), int(font * n / 240))
    return int(font * 1.15)


def _fill_word_blanks(doc: Document, brief, facts=None) -> list[str]:
    notes: list[str] = []
    for tbl in list(doc.element.body.iter(qn("w:tbl"))):
        _settle_table_note(tbl)
    for el in doc.element.body.iter(qn("w:p")):
        _fill_hints(el, brief)
        _swap_tokens(el, brief)
        _fill_para_blanks(el, brief)
        _fill_label_gap(el, brief)
    for tbl in list(doc.element.body.iter(qn("w:tbl"))):
        _swap_tokens(tbl, brief)
        _fill_amount_pairs(tbl, brief)
        for note in _fill_empty_label_cells(tbl, brief, facts):
            if note not in notes:
                notes.append(note)
        # 空格填完才知道字有没有超出空白稿的固定行高
        _relax_exact_rows(tbl)
    return notes


def _hint_value(hint: str, brief) -> str:
    if "项目名称" in hint:
        return (brief.projectName or "").strip()
    if "投标人" in hint:
        return (brief.bidderName or "").strip()
    return (brief.tenderer or "").strip()


def _fill_hints(el, brief) -> None:
    blob = _el_text(el)
    for hint in _HINTS:
        if hint not in blob:
            continue
        value = _hint_value(hint, brief)
        if not value:
            continue
        runs = [r for r in el.iter(qn("w:r"))]
        walked = 0
        start_run = 0
        pos = blob.find(hint)
        for i, run in enumerate(runs):
            n = len(_run_text(run))
            if walked + n > pos:
                start_run = i
                break
            walked += n
        placed = False
        for prev in reversed(runs[:start_run]):
            if _run_under(prev) and not re.sub(r"[\s_＿　]", "", _run_text(prev)):
                _seat_run(prev, value)
                placed = True
                break
        if not placed:
            _replace_span(el, hint, value)
            continue
        _replace_span(el, hint, "")
        _drop_blank_tab(prev)


def _replace_span(el, hint: str, value: str) -> None:
    """提示语可能拆在相邻的几个 w:t 里，按整段位置删或替换。"""
    nodes = [t for t in el.iter(qn("w:t")) if t.text]
    blob = "".join(t.text or "" for t in nodes)
    at = blob.find(hint)
    if at < 0:
        return
    end = at + len(hint)
    walked = 0
    first = True
    for node in nodes:
        n = len(node.text or "")
        a, b = walked, walked + n
        walked = b
        if b <= at or a >= end:
            continue
        left = (node.text or "")[: max(0, at - a)]
        right = (node.text or "")[max(0, end - a) :]
        if first:
            node.text = left + value + right
            first = False
        else:
            node.text = left + right


def _run_text(run) -> str:
    return "".join(t.text or "" for t in run.findall(qn("w:t")))


def _run_under(run) -> bool:
    rpr = run.find(qn("w:rPr"))
    if rpr is None:
        return False
    underline = rpr.find(qn("w:u"))
    if underline is None:
        return False
    return (underline.get(qn("w:val")) or "single") not in {"none", "0", "false"}


def _seat_run(run, text: str) -> None:
    """字从横线起点的第 2 格写。下划线制表符会把字顶到线尾。"""
    for tab in list(run.findall(qn("w:tab"))):
        run.remove(tab)
    body = (text or "").strip()
    _set_run_text(run, "\u3000" + body)
    _drop_blank_tab(run)


def _drop_blank_tab(run) -> None:
    nxt = run.getnext()
    while nxt is not None and nxt.tag == qn("w:r"):
        follow = nxt.getnext()
        if re.sub(r"[\s_＿　]", "", _run_text(nxt)):
            return
        if _run_under(nxt) and nxt.find(qn("w:tab")) is not None:
            parent = nxt.getparent()
            if parent is not None:
                parent.remove(nxt)
            return
        nxt = follow


def _set_run_text(run, text: str) -> None:
    nodes = list(run.findall(qn("w:t")))
    if not nodes:
        node = OxmlElement("w:t")
        run.append(node)
        nodes = [node]
    nodes[0].text = text
    if text[:1].isspace() or text[-1:].isspace():
        nodes[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    for extra in nodes[1:]:
        extra.text = ""


def _valid_days_text(brief) -> str:
    days = getattr(brief, "bidValidityDays", None)
    try:
        n = int(days)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    return f"自投标截止之日起{n}日历天"


def _blank_value(label: str, brief) -> str:
    tail = (label or "").rstrip(" ：:")
    if any(k in tail for k in ("签字", "盖章", "盖单位")):
        return ""
    if tail.endswith("委托代理人") or tail.endswith("法定代表人"):
        return ""
    pairs = (
        ("联合体牵头人", brief.bidderName),
        ("招标编号", brief.tenderNo),
        ("投标人名称", brief.bidderName),
        ("投标范围", getattr(brief, "bidContent", "")),
        ("投标质量", getattr(brief, "quality", "")),
        ("投标有效期", _valid_days_text(brief)),
        ("缺陷责任期", getattr(brief, "defectPeriod", "")),
        ("单位名称", brief.bidderName),
        ("企业名称", brief.bidderName),
        ("项目名称", brief.projectName),
        ("项目经理", getattr(brief, "pmName", "")),
        ("邮政编码", brief.bidderPostcode),
        ("姓名", getattr(brief, "legalPersonName", "")),
        ("传真", brief.bidderFax),
        ("邮箱", brief.bidderEmail),
        ("网址", brief.bidderWebsite),
        ("电话", brief.bidderPhone),
        ("地址", brief.bidderAddress),
        ("投标人", brief.bidderName),
    )
    for key, val in pairs:
        if not tail.endswith(key):
            continue
        if key == "电话" and any(k in tail for k in ("发包人", "监理", "手机")):
            continue
        if key == "地址" and "发包人" in tail:
            continue
        text = (val or "").strip()
        if text:
            return text
    return ""


def _fill_para_blanks(el, brief) -> None:
    runs = [r for r in el.iter(qn("w:r"))]
    compact = re.sub(r"\s+", "", "".join(_run_text(r) for r in runs))
    # 年月日留原样，避免把邮箱填进「年 月 日」
    if "年" in compact and "月" in compact and "日" in compact:
        return
    if len(compact) > 48:
        return
    acc = ""
    for run in runs:
        if _run_under(run) and not re.sub(r"[\s_＿　]", "", _run_text(run)):
            value = _blank_value(re.sub(r"\s+", "", acc)[-16:], brief)
            if value:
                _seat_run(run, value)
            acc = ""
            continue
        acc += _run_text(run)


def _fill_label_gap(el, brief) -> None:
    """整段只有标签时，把值接在标签后面，不改别的行。"""
    blob = re.sub(r"\s+", "", _el_text(el))
    if "年" in blob and "月" in blob:
        return
    pairs = (
        ("招标编号：", (getattr(brief, "tenderNo", "") or "").strip()),
        ("招标编号:", (getattr(brief, "tenderNo", "") or "").strip()),
        ("投标人或联合体牵头人：", (brief.bidderName or "").strip()),
        ("投标人或联合体牵头人:", (brief.bidderName or "").strip()),
        ("投标人或联合体牵头人：（盖单位章）", (brief.bidderName or "").strip()),
    )
    for label, value in pairs:
        if not value or value in blob or blob != label:
            continue
        nodes = [t for t in el.iter(qn("w:t")) if t.text]
        if not nodes:
            continue
        nodes[-1].text = (nodes[-1].text or "") + value
        return


_FEE_ATTRS = (
    ("安全生产措施费", "safetyFeeYuan"),
    ("暂列金额", "provisionalYuan"),
    ("专业工程暂估价", "primeCostYuan"),
    ("投标保证金", "bidBondYuan"),
)


def _amount_pair(amount) -> tuple[str, str]:
    if amount is None:
        return "", ""
    try:
        from api.services.tenders.money import rmb_figure, rmb_uppercase

        return rmb_uppercase(amount), rmb_figure(amount)
    except (ValueError, ArithmeticError):
        return "", ""


def _row_amount(blob: str, brief):
    for key, attr in _FEE_ATTRS:
        if key in blob:
            return getattr(brief, attr, None)
    if "投标总报价" in blob and "扣除" not in blob and not any(k in blob for k, _a in _FEE_ATTRS):
        price = getattr(brief, "bidPriceYuan", 0) or 0
        return price if price > 0 else None
    return None



def _fill_after_mark(cells, plains: list[str], mark: str, value: str) -> None:
    if not value:
        return
    for i, plain in enumerate(plains):
        if plain not in {mark, f"{mark}：", f"{mark}:"}:
            continue
        for j in range(i + 1, len(cells)):
            nxt = plains[j]
            if nxt:
                return
            _put_cell_text(cells[j], value)
            plains[j] = value
            return


def _fill_amount_pairs(tbl, brief) -> None:
    """附录表里的大写、小写仍写在原来的格子，不把表拆成段落。"""
    for tr in tbl.findall(qn("w:tr")):
        cells = tr.findall(qn("w:tc"))
        plains = [_cell_plain(tc) for tc in cells]
        upper, lower = _amount_pair(_row_amount("".join(plains), brief))
        if not upper:
            continue
        _fill_after_mark(cells, plains, "大写", upper)
        _fill_after_mark(cells, plains, "小写", lower)


def _pm_cell_value(label: str, brief) -> str:
    tail = (label or "").rstrip(" ：:")
    if tail.endswith("注册证号"):
        return (getattr(brief, "pmCertNo", "") or "").strip()
    if tail.endswith("级别"):
        return (getattr(brief, "pmGrade", "") or "").strip()
    if tail.endswith("姓名"):
        return (getattr(brief, "pmName", "") or "").strip()
    return ""


def _fill_empty_label_cells(tbl, brief, facts=None) -> list[str]:
    notes: list[str] = []
    person = _is_person_sheet(tbl)
    blob = "".join(_cell_plain(tc) for tc in tbl.iter(qn("w:tc")))
    sheet = "work" if "工作经历" in blob else ("pm" if person else "company")
    fact_fill = None
    if facts:
        from api.services.tenders.facts import fact_fill as _fact_fill

        fact_fill = _fact_fill
    for tr in tbl.findall(qn("w:tr")):
        cells = tr.findall(qn("w:tc"))
        plains = [_cell_plain(tc) for tc in cells]
        joined = "".join(plains)
        pm_row = person or "项目经理" in joined or "注册证号" in joined
        row_sheet = sheet
        if sheet != "work" and pm_row:
            row_sheet = "pm"
        prev = ""
        for tc, plain in zip(cells, plains):
            if not plain and prev:
                value = _pm_cell_value(prev, brief) if pm_row else ""
                # 简历没维护项目经理时，不要拿法定代表人姓名去填
                if not value and not person and not (pm_row and prev.rstrip(" ：:").endswith("姓名")):
                    value = _blank_value(prev, brief)
                if not value and fact_fill is not None:
                    value, note = fact_fill(prev, row_sheet, facts)
                    if note and note not in notes:
                        notes.append(note)
                if value:
                    _put_cell_text(tc, value)
                prev = ""
                continue
            prev = plain[-24:]
    return notes


def _put_cell_text(tc, value: str) -> None:
    nodes = list(tc.iter(qn("w:t")))
    if nodes:
        nodes[0].text = value
        for extra in nodes[1:]:
            extra.text = ""
        return
    p = tc.find(qn("w:p"))
    if p is None:
        p = OxmlElement("w:p")
        tc.append(p)
    run = OxmlElement("w:r")
    node = OxmlElement("w:t")
    node.text = value
    run.append(node)
    p.append(run)


def _dash_after(doc: Document, para, key: str, title: str) -> None:
    from api.services.tenders.placeholders import draw_placeholder_box
    from api.services.tenders.schema import PlaceholderItem

    draw_placeholder_box(doc, PlaceholderItem(key=key, title=title or "待补资料"), after=para)


def _attach_library(doc: Document, brief, media: dict | None) -> list[str]:
    """只认「附：法定代表人身份证」这种写明要贴的位置。其它附件看 attachToSection。"""
    id_note = _paste_id_pages(doc, brief, media)
    return [id_note] if id_note else []


_QUOTE_COLS = (
    ("规格", "spec"),
    ("型号", "spec"),
    ("单位", "unit"),
    ("数量", "qty"),
    ("单价", "price"),
    ("合价", "amount"),
    ("总价", "amount"),
    ("金额", "amount"),
    ("序号", "seq"),
    ("项目内容", "name"),
    ("项目名称", "name"),
    ("分项", "name"),
    ("名称", "name"),
)
_PERF_COLS = (
    ("合同金额", "amount"),
    ("合同价格", "amount"),
    ("签约合同价", "amount"),
    ("合同价", "amount"),
    ("买方联系人", "contact"),
    ("联系人", "contact"),
    ("买方", "client"),
    ("业主", "client"),
    ("发包", "client"),
    ("概况", "summary"),
    ("履约", "summary"),
    ("规格", "spec"),
    ("备注", "note"),
    ("工程名称", "projectName"),
    ("项目名称", "projectName"),
    ("序号", "seq"),
)
_BLANK_SKIP = ("签字", "盖章", "盖单位", "年月日", "序号", "大写", "小写")


def _direct_children(el, tag: str) -> list:
    return [child for child in el if child.tag == qn(tag)]


def _col_role(text: str, pairs: tuple) -> str:
    n = re.sub(r"\s+", "", text or "")
    for key, role in pairs:
        if key in n:
            return role
    return ""


def _num_text(value, digits: int = 2) -> str:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    if digits == 0 or abs(n - round(n)) < 1e-6:
        return str(int(round(n)))
    return f"{n:.{digits}f}"


def _quote_lines(brief) -> list:
    out = []
    for line in getattr(brief, "quoteLines", None) or []:
        if (getattr(line, "name", "") or "").strip():
            out.append(line)
    return out


def _perf_lines(brief) -> list:
    from api.services.tenders.performance import bid_performance_lines

    return [
        line
        for line in bid_performance_lines(getattr(brief, "performanceLines", None) or [])
        if (line.projectName or "").strip()
    ]


def _tech_text(brief) -> str:
    chunks = []
    note = (getattr(brief, "techPlanNote", "") or "").strip()
    if note:
        chunks.append(note)
    for item in getattr(brief, "outlineItems", None) or []:
        body = (getattr(item, "draftBody", "") or "").strip()
        if not body:
            continue
        title = (getattr(item, "title", "") or "").strip()
        chunks.append(f"{title}\n{body}" if title else body)
    return "\n\n".join(chunks).strip()


def _is_quote_header(plains: list[str]) -> bool:
    blob = "".join(plains)
    if "序号" not in blob:
        return False
    if not any(k in blob for k in ("单价", "合价", "金额", "总价")):
        return False
    if not any(k in blob for k in ("名称", "项目", "分项", "内容")):
        return False
    if "安全生产" in blob or "投标总报价" in blob:
        return False
    return True


_PERSON_MARKS = ("工作经历", "简历", "参加过的", "拟在本工程", "建造师", "安全生产考核")


def _is_person_blob(blob: str) -> bool:
    return any(k in (blob or "") for k in _PERSON_MARKS)


def _near_person_title(tbl) -> bool:
    node = tbl.getprevious()
    steps = 0
    while node is not None and steps < 8:
        if node.tag == qn("w:tbl"):
            break
        if node.tag == qn("w:p"):
            text = re.sub(r"\s+", "", _el_text(node))
            if text and any(k in text for k in ("工作经历", "简历", "管理人员")):
                return True
            steps += 1
        node = node.getprevious()
    return False


def _is_person_sheet(tbl) -> bool:
    blob = "".join(_cell_plain(tc) for tc in tbl.iter(qn("w:tc")))
    return _is_person_blob(blob) or _near_person_title(tbl)


def _is_perf_header(plains: list[str]) -> bool:
    blob = "".join(plains)
    if _is_person_blob(blob):
        return False
    if "项目名称" not in blob and "工程名称" not in blob:
        return False
    return any(k in blob for k in ("合同", "业主", "发包", "买方", "履约"))


def _row_cells(tr) -> list:
    return _direct_children(tr, "w:tc")


def _header_index(rows: list, kind: str) -> int:
    check = _is_quote_header if kind == "quote" else _is_perf_header
    for i, tr in enumerate(rows[:3]):
        plains = [_cell_plain(tc) for tc in _row_cells(tr)]
        if check(plains):
            return i
    return -1


def _quote_value(line, role: str, index: int) -> str:
    if role == "seq":
        return (getattr(line, "seq", "") or "").strip() or str(index + 1)
    if role == "name":
        return (getattr(line, "name", "") or "").strip()
    if role == "spec":
        return (getattr(line, "spec", "") or "").strip()
    if role == "unit":
        return (getattr(line, "unit", "") or "").strip()
    if role == "qty":
        return _num_text(getattr(line, "qty", 0), 0)
    if role == "price":
        return _num_text(getattr(line, "unitPrice", 0))
    if role == "amount":
        return _num_text(getattr(line, "amount", 0))
    return ""


def _perf_value(line, role: str, index: int) -> str:
    if role == "seq":
        return str(index + 1)
    if role == "projectName":
        return (line.projectName or "").strip()
    if role == "amount":
        return _num_text(line.amountYuan)
    if role == "client":
        return (line.client or "").strip()
    if role == "contact":
        return (line.contact or "").strip()
    if role == "summary":
        return (line.summary or "").strip()
    if role == "spec":
        return (line.spec or "").strip()
    if role == "note":
        return (line.note or "").strip()
    return ""


def _clone_blank_row(tbl, src) -> object:
    new_tr = deepcopy(src)
    for tc in _row_cells(new_tr):
        for node in tc.iter(qn("w:t")):
            node.text = ""
    tbl.append(new_tr)
    return new_tr


def _write_grid(tbl, lines, kind: str) -> int:
    rows = _direct_children(tbl, "w:tr")
    hi = _header_index(rows, kind)
    if hi < 0:
        return -1
    pairs = _QUOTE_COLS if kind == "quote" else _PERF_COLS
    value_of = _quote_value if kind == "quote" else _perf_value
    roles = [_col_role(_cell_plain(tc), pairs) for tc in _row_cells(rows[hi])]
    need = "name" if kind == "quote" else "projectName"
    if need not in roles:
        return -1
    name_i = roles.index(need)
    empty = []
    for tr in rows[hi + 1 :]:
        cells = _row_cells(tr)
        plains = [_cell_plain(tc) for tc in cells]
        if name_i < len(plains) and plains[name_i].strip():
            continue
        empty.append(tr)
    if not lines:
        return 0
    while len(empty) < len(lines):
        src = empty[-1] if empty else rows[hi]
        empty.append(_clone_blank_row(tbl, src))
    written = 0
    for tr, line in zip(empty, lines):
        cells = _row_cells(tr)
        for i, role in enumerate(roles):
            if not role or i >= len(cells):
                continue
            if _cell_plain(cells[i]).strip() and role != "seq":
                continue
            if role == "seq" and _cell_plain(cells[i]).strip():
                continue
            val = value_of(line, role, written)
            if val:
                _put_cell_text(cells[i], val)
        written += 1
    return written


def _perf_form(tbl, line) -> bool:
    labels = []
    rows = _direct_children(tbl, "w:tr")
    for tr in rows:
        cells = _row_cells(tr)
        if cells:
            labels.append(_cell_plain(cells[0]))
    blob = "".join(labels)
    if "项目名称" not in blob:
        return False
    if _is_person_sheet(tbl):
        return False
    if not any(k in blob for k in ("合同价格", "签约合同价", "合同金额", "买方名称", "发包人")):
        return False
    amount = _num_text(line.amountYuan)
    pairs = (
        ("项目概况", (line.summary or "").strip()),
        ("履约", (line.summary or "").strip()),
        ("规格", (line.spec or "").strip()),
        ("买方联系人", (line.contact or "").strip()),
        ("联系人", (line.contact or "").strip()),
        ("买方名称", (line.client or "").strip()),
        ("业主", (line.client or "").strip()),
        ("发包人", (line.client or "").strip()),
        ("合同价格", amount),
        ("签约合同价", amount),
        ("合同金额", amount),
        ("备注", (line.note or "").strip()),
        ("项目名称", (line.projectName or "").strip()),
    )
    for tr in rows:
        cells = _row_cells(tr)
        if len(cells) < 2 or _cell_plain(cells[1]).strip():
            continue
        label = _cell_plain(cells[0])
        for key, val in pairs:
            if key in label and val:
                _put_cell_text(cells[1], val)
                break
    return True


def _fill_tech_table(tbl, text: str) -> int:
    rows = _direct_children(tbl, "w:tr")
    if not rows:
        return -1
    head = "".join(_cell_plain(tc) for tc in _row_cells(rows[0]))
    if not any(k in head for k in ("技术方案", "施工组织", "技术标")):
        return -1
    if not text:
        return 0
    for tr in rows:
        for tc in _row_cells(tr):
            plain = _cell_plain(tc)
            if plain.strip():
                continue
            _put_cell_text(tc, text)
            return 1
    return 0


def _table_in_cell(tbl) -> bool:
    parent = tbl.getparent()
    while parent is not None and parent.tag != qn("w:body"):
        if parent.tag == qn("w:tc"):
            return True
        parent = parent.getparent()
    return False


def _fill_slice_sheets(doc: Document, brief) -> list[str]:
    """报价、业绩、技术草稿只写进截件里已经有的空表。"""
    notes: list[str] = []
    quotes = _quote_lines(brief)
    perfs = _perf_lines(brief)
    tech = _tech_text(brief)
    quote_hit = False
    perf_hit = False
    tech_hit = False
    quote_n = 0
    perf_n = 0
    tech_n = 0
    for tbl in doc.element.body.iter(qn("w:tbl")):
        if _table_in_cell(tbl):
            continue
        if not quote_hit:
            n = _write_grid(tbl, quotes, "quote")
            if n >= 0:
                quote_hit = True
                quote_n = n
                continue
        if not perf_hit and not _is_person_sheet(tbl):
            n = _write_grid(tbl, perfs, "perf")
            if n >= 0:
                perf_hit = True
                perf_n = n
                continue
            if perfs and _perf_form(tbl, perfs[0]):
                perf_hit = True
                perf_n = 1
                continue
        if not tech_hit:
            n = _fill_tech_table(tbl, tech)
            if n >= 0:
                tech_hit = True
                tech_n = n
    if quote_hit and quote_n:
        notes.append(f"已写入截件报价表 {quote_n} 行")
    elif quote_hit:
        notes.append("截件有报价表，没有可填的分项")
    elif quotes:
        notes.append("截件没有报价空表，未写入分项")
    if perf_hit and perf_n:
        extra = ""
        if len(perfs) > perf_n:
            extra = f"，多出 {len(perfs) - perf_n} 条未写入"
        notes.append(f"已写入截件业绩表 {perf_n} 条{extra}")
    elif perf_hit:
        notes.append("截件有业绩表，没有可填的业绩")
    elif perfs:
        notes.append("截件没有业绩空表，未写入业绩")
    if tech_n:
        notes.append("已写入截件里的技术表")
    elif tech_hit and tech:
        notes.append("截件有技术表，但没有空格，未写入技术草稿")
    elif tech_hit:
        notes.append("截件有技术表，没有可填的技术草稿")
    elif tech:
        notes.append("截件没有技术空表，未写入技术草稿")
    return notes


def _open_blank_labels(doc: Document) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add(label: str) -> None:
        text = re.sub(r"\s+", "", label or "").strip("：:（）()")
        if not text or len(text) < 2 or len(text) > 18:
            return
        if any(k in text for k in _BLANK_SKIP):
            return
        if text in seen:
            return
        seen.add(text)
        found.append(text)

    for tbl in doc.element.body.iter(qn("w:tbl")):
        for tr in _direct_children(tbl, "w:tr"):
            plains = [_cell_plain(tc) for tc in _row_cells(tr)]
            blob = "".join(plains)
            if "序号" in blob and "名称" in blob:
                continue
            if len(plains) < 2 or len(plains) > 4:
                continue
            for i, plain in enumerate(plains[:-1]):
                if plains[i + 1].strip() or not plain.strip():
                    continue
                add(plain)
    for para in doc.paragraphs:
        n = re.sub(r"\s+", "", para.text or "")
        if 2 < len(n) <= 16 and (n.endswith("：") or n.endswith(":")):
            add(n)
    blob = "\n".join(p.text or "" for p in doc.paragraphs)
    for token in ("（项目名称）", "（招标人名称）", "（投标人名称）", "（采购人名称）"):
        if token in blob:
            add(token)
    return found


def _restart_page_after_toc(doc: Document) -> None:
    """目录和封面不编页，目录后的第一节从 1 开始。"""
    from api.services.tenders.format_rules import _add_page_field, _clear_runs

    children = [el for el in doc.element.body if el.tag != qn("w:sectPr")]
    pages = _split_pages(children)
    toc_i = _toc_page_index(pages)
    if toc_i is None or toc_i + 1 >= len(pages):
        return
    sect_el = None
    for el in pages[toc_i + 1]:
        found = _page_sect(el)
        if found is not None:
            sect_el = found
    if sect_el is None:
        return
    for old in list(sect_el.findall(qn("w:pgNumType"))):
        sect_el.remove(old)
    pg = OxmlElement("w:pgNumType")
    pg.set(qn("w:start"), "1")
    sect_el.append(pg)
    idx = None
    for i, sec in enumerate(doc.sections):
        if sec._sectPr is sect_el:
            idx = i
            break
    if idx is None:
        return
    # 封面、目录沿用原页脚，先抹掉邀请书印上去的 -185- 这类死页码
    for sec in doc.sections[:idx]:
        for node in sec.footer._element.iter(qn("w:t")):
            if re.fullmatch(r"-?\d{1,4}-?", re.sub(r"\s+", "", node.text or "")):
                node.text = ""
    footer = doc.sections[idx].footer
    footer.is_linked_to_previous = False
    para = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    _clear_runs(para)
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _add_page_field(para, font="宋体", size_pt=10.5)
