"""Keep the invitation's original forms; supplement only missing submission sections."""

from __future__ import annotations

import re
from copy import deepcopy
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from api.services.tenders.schema import BidBrief, OutlineItem


def word_format_section(path: Path) -> tuple[str, str]:
    """Read the real body heading (including Heading 3), excluding directory links."""
    from api.services.tenders.slice_rules import is_format_end, is_format_start

    if not path.is_file():
        return "", ""
    doc = Document(path)
    children = list(doc.element.body)
    starts = []
    for i, el in enumerate(children):
        if el.tag != qn("w:p") or _directory_entry(el):
            continue
        text = _text(el)
        if is_format_start(text):
            starts.append(i)
    if not starts:
        return "", ""
    start = starts[-1]
    end = next(
        (
            i
            for i in range(start + 1, len(children))
            if children[i].tag == qn("w:p")
            and not _directory_entry(children[i])
            and is_format_end(_text(children[i]))
        ),
        len(children),
    )
    lines = [_text(children[start])]
    lines.extend(_text(p) for el in children[start + 1 : end] for p in el.iter(qn("w:p")))
    return _text(children[start]), "\n".join(lines)


def _text(el) -> str:
    return "".join(n.text or "" for n in el.iter(qn("w:t"))).strip()


def _directory_entry(el) -> bool:
    style = el.find("w:pPr/w:pStyle", el.nsmap)
    return (
        "PAGEREF" in el.xml
        or el.find(".//w:r/w:tab", el.nsmap) is not None
        or next(el.iter(qn("w:hyperlink")), None) is not None
        or (style is not None and re.match(r"TOC\d*$", style.get(qn("w:val"), ""), re.I))
    )


def prefer_native_format(brief: BidBrief) -> bool:
    """Enforce the original-format choice before stale client flags select a builder."""
    from api.services.tenders.assets import tender_invitations_dir
    from api.services.tenders.slice_rules import is_format_start

    chapter = brief.outlineChapter
    section = ""
    if re.fullmatch(r"[a-f0-9]{10,16}", brief.invitationId or ""):
        heading, section = word_format_section(
            tender_invitations_dir() / f"{brief.invitationId}.docx"
        )
        if heading:
            chapter = heading
    if not is_format_start(chapter):
        return False
    brief.outlineChapter = chapter
    brief.layoutMode = "outline"
    brief.requirementsDriven = False
    # A previous requirements-only generation can leave synthetic group headings.
    # Recover the original chapter order and carry forward edits by section title.
    if section and (
        not brief.outlineItems or any(i.id.startswith("req-group-") for i in brief.outlineItems)
    ):
        from api.services.tenders.outline import extract_outline

        _, recovered = extract_outline(section)
        if recovered:
            old = {
                _key(i.title): i for i in brief.outlineItems if not i.id.startswith("req-group-")
            }
            merged = []
            for item in recovered:
                edited = old.pop(_key(item.title), None)
                merged.append(edited if edited is not None else item)
            merged.extend(old.values())
            brief.outlineItems = merged
    _supplement_outline(brief)
    _recover_styled_chapters(brief)
    return True


def _recover_styled_chapters(brief: BidBrief) -> None:
    """Recover actual Word chapter styles when text extraction lost their numbering."""
    from api.services.tenders.outline import classify_kind, is_outline_chapter
    from api.services.tenders.pdf_format import _word_format_file, toc_block_ids
    from api.services.tenders.slice_rules import is_format_start

    src = _word_format_file(brief.invitationId)
    if src is None:
        return
    doc = Document(src)
    toc = set(toc_block_ids(doc))
    keys = {_key(i.title) for i in brief.outlineItems}
    ranked = []
    for p in doc.paragraphs:
        text = p.text.strip()
        if (
            p._p in toc
            or _directory_entry(p._p)
            or not text
            or is_format_start(text)
            or re.sub(r"\s+", "", text)
            in {"目录", "投标文件", "响应文件", "商务标", "技术标", "封面格式"}
        ):
            continue
        rank = None
        style = p.style
        while style is not None:
            level = style.element.find("w:pPr/w:outlineLvl", style.element.nsmap)
            match = re.match(r"Heading\s*(\d+)$", style.name, re.I)
            if level is not None:
                rank = int(level.get(qn("w:val"), "9")) + 1
                break
            if match:
                rank = int(match.group(1))
                break
            style = style.base_style
        direct = p._p.find("w:pPr/w:outlineLvl", p._p.nsmap)
        if direct is not None:
            rank = int(direct.get(qn("w:val"), "9")) + 1
        item = OutlineItem(
            id="native-chapter-" + str(len(brief.outlineItems) + len(ranked)),
            title=text,
            rawTitle=text,
            kind=classify_kind(text),
        )
        if rank is not None and rank <= 9 and is_outline_chapter(item):
            ranked.append((rank, item))
    stack = []
    for rank, item in ranked:
        while stack and stack[-1] >= rank:
            stack.pop()
        stack.append(rank)
        if _key(item.title) not in keys:
            item.level = min(4, len(stack))
            item.tocEligible = True
            brief.outlineItems.append(item)
            keys.add(_key(item.title))


def _key(title: str) -> str:
    from api.services.tenders.outline import toc_title_key

    value = toc_title_key(title)
    value = value.replace("已标价的工程量清单", "已标价工程量清单")
    value = re.sub(r"^第[一二三四五六七八九十百0-9]+部分[、.．]?", "", value)
    value = re.sub(r"[（(](?:货物|工程|服务)[)）]$", "", value)
    # The directory names the form, while the body names the document. Share
    # their identity for lookup/supplement and retain rawTitle for display.
    return {"施工组织格式": "施工组织设计"}.get(value, value)


def _supplement_outline(brief: BidBrief) -> None:
    """Only named submission requirements and missing technical essentials are added."""
    from api.services.tenders.classifier import classify_content
    from api.services.tenders.outline import classify_kind
    from api.services.tenders.requirement_bid import invitation_text

    candidates = [
        (r.targetTitle, r.targetKind)
        for r in brief.bidRequirements
        if r.targetTitle and r.targetKind
    ]
    text = invitation_text(brief)
    forms = re.findall(r"《([^》]{2,40}(?:声明函|承诺函|承诺书)(?:[（(]货物[)）])?)》", text)
    forms.extend(
        line.strip()
        for line in text.splitlines()
        if re.fullmatch(
            r"[^：:。；;\n]{2,35}(?:声明函|承诺函|承诺书)(?:[（(]货物[)）])?", line.strip()
        )
        and not any(w in line for w in ("提供", "提交", "应当", "必须"))
    )
    candidates.extend((title, classify_kind(title)) for title in forms)
    # Full and technical volumes need actual technical sections even if the source
    # format supplies only business forms. They remain pending until drafted.
    for kind, title in (("tech_plan", "技术方案"), ("tech_dev", "技术偏离表")):
        if not any(i.kind == kind for i in brief.outlineItems):
            candidates.append((title, kind))
    keys = {_key(i.title) for i in brief.outlineItems}
    ids = {i.id for i in brief.outlineItems}
    for title, kind in candidates:
        named_form = bool(
            re.fullmatch(
                r".{2,35}(?:声明函|承诺函|承诺书)(?:[（(]货物[)）])?",
                title,
            )
        )
        if _key(title) in keys or (not named_form and classify_content(title).role != "chapter"):
            continue
        index = len(brief.outlineItems) + 1
        while f"native-extra-{index}" in ids:
            index += 1
        item = OutlineItem(
            id=f"native-extra-{index}",
            title=title,
            rawTitle=title,
            kind=kind or "commitment_copy",
            source="generate",
        )
        brief.outlineItems.append(item)
        ids.add(item.id)
        keys.add(_key(title))


def selected_items(brief: BidBrief, volume: str) -> list[OutlineItem]:
    from api.services.tenders.categories import is_volume_label

    return [
        i
        for i in brief.outlineItems
        if not i.skipped
        and not is_volume_label(i.title)
        and (volume not in {"business", "technical"} or section_volume(i) == volume)
    ]


def section_volume(item: OutlineItem) -> str:
    from api.services.tenders.categories import item_volume

    if item.kind in {"scan", "unknown", "company"} and any(
        word in item.title for word in ("技术证明", "技术资料", "技术方案", "技术参数")
    ):
        return "technical"
    return item_volume(kind=item.kind, title=item.title, key=item.id)


def body_sections(doc, brief: BidBrief) -> list[tuple]:
    """Body form locations used for filling; these are not necessarily TOC chapters."""

    children = list(doc.element.body)
    block = _body_toc_block(doc, brief)
    start = children.index(block[-1]) + 1 if block else 0
    keys = {_key(i.title): i for i in brief.outlineItems if not i.skipped}
    native = _native_chapters(brief)
    out = []
    for el in children[start:]:
        if el.tag not in {qn("w:p"), qn("w:tbl")} or _directory_entry(el):
            continue
        paragraphs = [el] if el.tag == qn("w:p") else list(el.iter(qn("w:p")))
        for p in paragraphs:
            title = _text(p)
            item = keys.get(_key(title))
            if native and el.tag == qn("w:p") and _physical_chapter(p, doc):
                item = _physical_item(p, doc, item)
            if item is not None and len(title) <= 70:
                out.append((el, p, item))
                break
    return out


def _native_chapters(brief) -> bool:
    from api.services.tenders.slice_rules import is_format_start

    return is_format_start(brief.outlineChapter or "")


def _body_toc_block(doc, brief):
    from api.services.tenders.pdf_format import toc_block_ids

    block = toc_block_ids(doc)
    if not _native_chapters(brief):
        return block
    # A plain, unlisted front letter may be swallowed by permissive TOC parsing.
    # Its following salutation/body/table distinguishes it from a directory row.
    for n, el in enumerate(block[1:], 1):
        if not _physical_chapter(el, doc):
            continue
        following = el.getnext()
        if following is None:
            continue
        text = _text(following)
        if following.tag == qn("w:tbl") or (
            text
            and re.search(r"[：:。；;]", text)
            and not _physical_chapter(following, doc)
            and not _cover_signoff(text)
        ):
            return block[:n]
    return block


def _physical_chapter(p, doc) -> bool:
    """Only standalone chapter paragraphs; never field labels inside a table."""
    from api.services.tenders.format_rules import peel_toc_lead

    text = _text(p)
    if (
        not text
        or len(text) > 70
        or _directory_entry(p)
        or _cover_signoff(text)
        or _format_banner(text)
        or re.search(r"[：:。；;]|【待|尚未响应", text)
    ):
        return False
    bare = _key(peel_toc_lead(text))
    if bare in {"目录", "目次", "投标文件", "响应文件", "商务标", "技术标"}:
        return False
    # These forms are genuine chapters even when Word gave them Normal style.
    if re.fullmatch(
        r"投标函(?:附录|及投标函附录)?|响应函|法定代表人身份证明|授权委托书|"
        r"投标保证金|已标价(?:的)?工程量清单|项目管理机构|资格审查资料",
        bare,
    ):
        return True
    if _is_body_chapter(p) or re.match(r"^[（(][一二三四五六七八九十]+[）)]\S", text):
        return True
    from api.services.tenders.word_structure import numbering_info

    numbered = numbering_info(doc, p)
    if numbered is not None and numbered[2].startswith("chineseCounting"):
        return True
    style = Paragraph(p, doc).style
    while style is not None:
        outline = style.element.find("w:pPr/w:outlineLvl", style.element.nsmap)
        if outline is not None and int(outline.get(qn("w:val"), "9")) < 9:
            return True
        style = style.base_style
    return False


def _physical_item(p, doc, existing=None):
    from hashlib import sha1

    from api.services.tenders.outline import _extracted_item_level, classify_kind

    title = _text(p)
    # Explicit numbering is stronger evidence than stale outlineItems.level.
    numbered = re.match(r"^(?:第.+?[章节部分]|[一二三四五六七八九十]+、|[（(].+[）)])", title)
    level = _extracted_item_level(title) if numbered else 1
    if not numbered:
        para = Paragraph(p, doc)
        style = para.style
        while style is not None:
            outline = style.element.find("w:pPr/w:outlineLvl", style.element.nsmap)
            if outline is not None:
                level = min(4, int(outline.get(qn("w:val"), "0")) + 1)
                break
            style = style.base_style
    if existing is not None:
        return existing.model_copy(update={"rawTitle": title, "level": level, "tocEligible": True})
    return OutlineItem(
        id="native-body-" + sha1(title.encode()).hexdigest()[:12],
        title=title,
        rawTitle=title,
        level=level,
        kind=classify_kind(title),
        tocEligible=True,
    )


def body_headings(doc, brief: BidBrief) -> list[tuple]:
    """TOC targets are real top-level chapter paragraphs, never table cell labels."""
    from api.services.tenders.outline import is_outline_chapter

    seen = set()
    result = []
    for el, p, item in body_sections(doc, brief):
        key = _key(item.title)
        physical = _native_chapters(brief) and _physical_chapter(p, doc)
        if (
            el.tag != qn("w:p")
            or (key in seen and not physical)
            or not (physical or is_outline_chapter(item))
        ):
            continue
        # 封面「投标人：（加盖公章）」不是章节，不能当目录第一条
        if _cover_signoff(_text(p)):
            continue
        seen.add(key)
        result.append((el, p, item))
    return result


def _format_banner(text: str) -> bool:
    n = re.sub(r"\s+", "", text or "")
    if n in {"封面", "封面格式"}:
        return True
    return "投标文件格式" in n or "响应文件格式" in n


def _is_body_chapter(el) -> bool:
    if el.tag != qn("w:p") or _directory_entry(el):
        return False
    style = el.find("w:pPr/w:pStyle", el.nsmap)
    style_id = style.get(qn("w:val"), "") if style is not None else ""
    if re.match(r"(Heading|标题)\s*\d", style_id, re.I):
        return True
    if el.find("w:pPr/w:outlineLvl", el.nsmap) is not None:
        return True
    n = re.sub(r"\s+", "", _text(el))
    return bool(
        re.match(r"^(?:第[一二三四五六七八九十0-9]+[章节部分]|[一二三四五六七八九十]{1,3}、)", n)
    )


def _front_signoffs(doc) -> list:
    """封面落款。目录标题前后都算封面，正文章节之后的签章行不算。"""
    from api.services.tenders.pdf_format import _is_toc_heading

    found = []
    seen_toc = False
    for el in doc.element.body:
        if el.tag == qn("w:sectPr"):
            break
        if el.tag != qn("w:p"):
            if seen_toc and found and el.tag == qn("w:tbl"):
                break
            continue
        if _is_toc_heading(_text(el)):
            seen_toc = True
            continue
        text = _text(el)
        if not text:
            continue
        # 落款里「年」和「月」之间常有制表符，不能当成目录行跳过
        if _cover_signoff(text):
            found.append(el)
            continue
        if _directory_entry(el):
            continue
        # 「第六章 投标文件格式」是格式章名，不是正文，封面落款还在它后面
        if _format_banner(text):
            continue
        if _is_body_chapter(el) or (
            seen_toc and ("。" in text or len(re.sub(r"\s+", "", text)) > 40)
        ):
            break
    return found


def _bare_page_break(el) -> bool:
    if el.tag != qn("w:p") or _text(el):
        return False
    if el.find("w:pPr/w:sectPr", el.nsmap) is not None:
        return False
    return any(n.get(qn("w:type")) == "page" for n in el.iter(qn("w:br")))


def _clear_page_break_before(el) -> None:
    ppr = el.find(qn("w:pPr"))
    if ppr is None:
        return
    for node in list(ppr.findall(qn("w:pageBreakBefore"))):
        ppr.remove(node)


def _park_toc_after_cover(title, signoffs: list) -> None:
    """目录标题挪到封面落款之后。落款段落本身留在原位。"""
    if title is None or not signoffs:
        return
    last = None
    passed = False
    for el in title.getparent():
        if el is title:
            passed = True
            continue
        if not passed or el not in signoffs:
            continue
        last = el
    if last is None:
        return
    last.addnext(title)


def _drop_breaks_inside_cover(title, signoffs: list) -> None:
    """封面字段之间的分页会把盖章签字推到目录页，去掉。目录前的分页留着。"""
    if title is None or not signoffs:
        return
    parent = title.getparent()
    if parent is None:
        return
    signoff_ids = set(signoffs)
    pending = []
    for el in list(parent):
        if el is title:
            break
        if not _bare_page_break(el):
            continue
        later = False
        seen = False
        for nxt in parent:
            if nxt is el:
                seen = True
                continue
            if not seen:
                continue
            if nxt is title:
                break
            if nxt in signoff_ids:
                later = True
                break
        if later:
            pending.append(el)
    for el in pending:
        parent.remove(el)


def _cover_elements(doc, brief: BidBrief) -> list:
    children = [el for el in doc.element.body if el.tag != qn("w:sectPr")]
    block = _body_toc_block(doc, brief)
    heads = body_sections(doc, brief)
    stop = block[0] if block else (heads[0][0] if heads else None)
    if stop is None:
        return []
    nodes = children[: children.index(stop)]
    for el in _front_signoffs(doc):
        if el not in nodes:
            nodes.append(el)
    if not any(
        "投标文件" in re.sub(r"\s+", "", _text(el)) or "响应文件" in re.sub(r"\s+", "", _text(el))
        for el in nodes
    ):
        return []
    signoffs = set(_front_signoffs(doc))
    while (
        nodes
        and nodes[-1] not in signoffs
        and not _text(nodes[-1])
        and any(n.get(qn("w:type")) == "page" for n in nodes[-1].iter(qn("w:br")))
    ):
        nodes.pop()
    return nodes


def original_cover(doc, brief: BidBrief) -> list:
    nodes = _cover_elements(doc, brief)
    if not nodes:
        return []
    copied = [deepcopy(el) for el in nodes if el.tag != qn("w:sectPr")]
    for el in copied:
        for sect in list(el.iter(qn("w:sectPr"))):
            sect.getparent().remove(sect)
    while (
        copied
        and not _text(copied[-1])
        and any(n.get(qn("w:type")) == "page" for n in copied[-1].iter(qn("w:br")))
    ):
        copied.pop()
    return copied


def supplement_sections(doc, brief: BidBrief, volume: str, *, media=None) -> list[str]:
    """Append absent sections in the same document, preserving all native XML/parts."""
    from api.services.tenders.assemble import _write_body, _write_heading
    from api.services.tenders.assets import find_chapter5_template
    from api.services.tenders.modules import MODULE_KINDS, render_module

    present = {_key(item.title) for _, _, item in body_sections(doc, brief)}
    template_path = find_chapter5_template()
    template = Document(template_path) if template_path is not None else None
    notes = []
    for item in selected_items(brief, volume):
        if _key(item.title) in present:
            continue
        doc.add_page_break()
        copied = _copy_template_section(doc, template, item) if template is not None else False
        if not copied:
            _write_heading(doc, item.rawTitle or item.title, brief.documentFormat)
            if item.kind in MODULE_KINDS:
                notes.extend(render_module(doc, brief, item, media=media))
            else:
                # Declaration eligibility and commitments are never asserted from
                # a generic default; the owner must confirm these facts.
                _write_body(
                    doc,
                    item.draftBody or f"【待补充】请核实并填写{item.title}。",
                    brief.documentFormat,
                )
                _write_body(
                    doc,
                    "投标人（盖章）：\n法定代表人或委托代理人（签字）：\n日期：",
                    brief.documentFormat,
                )
        present.add(_key(item.title))
        notes.append(
            f"原版格式章缺少「{item.title}」，已用内置对应格式补齐"
            + ("（正文及适用条件请核实）" if item.kind == "commitment_copy" else "")
        )
    return notes


def normalize_bookmarks(doc) -> None:
    """Repair invalid duplicate source bookmarks on the output copy only."""
    names = set()
    pending: dict[str, list] = {}
    opened = {}
    next_id = 0
    for node in list(doc.element.body.iter()):
        if node.tag == qn("w:bookmarkStart"):
            old_id = node.get(qn("w:id"), "")
            name = node.get(qn("w:name"), "")
            assigned = None
            if name and name not in names:
                names.add(name)
                assigned = str(next_id)
                next_id += 1
                node.set(qn("w:id"), assigned)
                opened[assigned] = node
            else:
                node.getparent().remove(node)
            pending.setdefault(old_id, []).append(assigned)
        elif node.tag == qn("w:bookmarkEnd"):
            stack = pending.get(node.get(qn("w:id"), ""), [])
            assigned = stack.pop() if stack else None
            if assigned is None:
                node.getparent().remove(node)
            else:
                node.set(qn("w:id"), assigned)
                opened.pop(assigned, None)
    # An unclosed source bookmark cannot safely be a TOC target.
    for node in opened.values():
        node.getparent().remove(node)


def _copy_template_section(doc, template, item: OutlineItem) -> bool:
    """Copy one matching blank form, including its styles, lists and image links."""
    children = list(template.element.body)
    # Only actual heading paragraphs can bound the selected built-in section.
    from api.services.tenders.outline import classify_kind
    from api.services.tenders.pdf_format import toc_block_ids

    toc = set(toc_block_ids(template))
    headings = [
        (i, el)
        for i, el in enumerate(children)
        if el.tag == qn("w:p")
        and el not in toc
        and not _directory_entry(el)
        and len(_text(el)) <= 60
        and (
            classify_kind(_text(el)) != "unknown"
            or el.find("w:pPr/w:outlineLvl", el.nsmap) is not None
            or (
                Paragraph(el, template).style is not None
                and Paragraph(el, template).style.name.startswith("Heading")
            )
        )
    ]
    hits = [(n, i, el) for n, (i, el) in enumerate(headings) if _key(_text(el)) == _key(item.title)]
    if len(hits) != 1:
        return False
    n, start, _ = hits[0]
    end = headings[n + 1][0] if n + 1 < len(headings) else len(children)
    nodes = [deepcopy(el) for el in children[start:end] if el.tag != qn("w:sectPr")]
    # Skip unsupported embedded parts rather than introduce dangling relationships.
    for el in nodes:
        for node in el.iter():
            for attr, rid in node.attrib.items():
                if attr.startswith(
                    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
                ):
                    rel = template.part.rels.get(rid)
                    if rel is None or (not rel.is_external and not rel.reltype.endswith("/image")):
                        return False
    numbering_map = {}
    for el in nodes:
        _import_form_xml(doc, template, el, numbering_map=numbering_map)
        doc.element.body.insert_element_before(el, "w:sectPr")
    return True


def _import_form_xml(doc, template, el, *, numbering_map: dict) -> None:
    """Namespace imported style/list IDs so the original document definitions win."""
    style_cache = set()

    def style_id(source_id):
        dest_id = "native补章_" + source_id
        if (
            dest_id in style_cache
            or doc.styles.element.find(f'w:style[@w:styleId="{dest_id}"]', doc.element.nsmap)
            is not None
        ):
            return dest_id
        source = template.styles.element.find(
            f'w:style[@w:styleId="{source_id}"]', template.element.nsmap
        )
        if source is None:
            return source_id
        style_cache.add(dest_id)
        copied = deepcopy(source)
        copied.set(qn("w:styleId"), dest_id)
        name = copied.find(qn("w:name"))
        if name is not None:
            name.set(qn("w:val"), dest_id)
        for tag in ("basedOn", "next", "link"):
            ref = copied.find(qn("w:" + tag))
            if ref is not None:
                ref.set(qn("w:val"), style_id(ref.get(qn("w:val"))))
        doc.styles.element.append(copied)
        return dest_id

    numbering = doc.part.numbering_part.element
    source_numbering = template.part.numbering_part.element
    for node in list(el.iter()):
        if node.tag in {qn("w:bookmarkStart"), qn("w:bookmarkEnd"), qn("w:sectPr")}:
            node.getparent().remove(node)
            continue
        if node.tag in {qn("w:pStyle"), qn("w:rStyle"), qn("w:tblStyle")}:
            node.set(qn("w:val"), style_id(node.get(qn("w:val"))))
        if node.tag == qn("w:numId") and node.get(qn("w:val")) != "0":
            source_id = node.get(qn("w:val"))
            if source_id in numbering_map:
                node.set(qn("w:val"), numbering_map[source_id])
                continue
            num = source_numbering.find(
                f'w:num[@w:numId="{node.get(qn("w:val"))}"]', template.element.nsmap
            )
            if num is not None:
                abstract_id = num.find(qn("w:abstractNumId")).get(qn("w:val"))
                abstract = source_numbering.find(
                    f'w:abstractNum[@w:abstractNumId="{abstract_id}"]', template.element.nsmap
                )
                if abstract is not None:
                    new_abs = str(
                        max(
                            (
                                int(n.get(qn("w:abstractNumId")))
                                for n in numbering.findall(qn("w:abstractNum"))
                            ),
                            default=-1,
                        )
                        + 1
                    )
                    new_num = str(
                        max(
                            (int(n.get(qn("w:numId"))) for n in numbering.findall(qn("w:num"))),
                            default=0,
                        )
                        + 1
                    )
                    a, b = deepcopy(abstract), deepcopy(num)
                    a.set(qn("w:abstractNumId"), new_abs)
                    b.set(qn("w:numId"), new_num)
                    b.find(qn("w:abstractNumId")).set(qn("w:val"), new_abs)
                    numbering.insert_element_before(a, "w:num")
                    numbering.append(b)
                    node.set(qn("w:val"), new_num)
                    numbering_map[source_id] = new_num
        for attr, rid in list(node.attrib.items()):
            if not attr.startswith(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
            ):
                continue
            rel = template.part.rels[rid]
            if rel.is_external:
                target = doc.part.relate_to(rel.target_ref, rel.reltype, is_external=True)
            else:
                target, _ = doc.part.get_or_add_image(BytesIO(rel.target_part.blob))
            node.set(attr, target)


_FORMAT_TOC_MARKS = (
    "投标函",
    "法定代表人身份证明",
    "授权委托书",
    "投标保证金",
    "工程量清单",
    "项目管理机构",
    "拟分包",
    "资格审查资料",
    "其他资料",
    "其他材料",
    "施工组织",
)


def _toc_visible_title(text: str, el=None) -> str:
    title = (text or "").split("\t", 1)[0]
    title = re.sub(r"[.．·…]{2,}\s*\d*$", "", title)
    title = re.sub(r"\s+\d{1,4}$", "", title).strip()
    # 页码在域结果里，和标题连成「资格审查资料8」，没有制表符。
    if el is not None and "PAGEREF" in el.xml:
        title = re.sub(r"\d{1,4}$", "", title).strip()
    return title


def _toc_para_level(el, text: str, doc=None) -> int:
    from api.services.tenders.outline import _extracted_item_level
    from api.services.tenders.pdf_format import _outline_level

    if doc is not None:
        from api.services.tenders.word_structure import numbering_info

        info = numbering_info(doc, el)
        if info is not None:
            return info[-1]

    shown = _toc_visible_title(text)
    compact = re.sub(r"\s+", "", shown)
    if re.match(r"^[一二三四五六七八九十]{1,3}、", compact) or re.match(
        r"^[（(][一二三四五六七八九十0-9]+[)）]", compact
    ):
        return _extracted_item_level(shown)
    styled = _outline_level(el)
    if styled:
        return styled
    style = el.find("w:pPr/w:pStyle", el.nsmap)
    if style is not None:
        hit = re.search(r"(?:toc|目录)\s*(\d)", style.get(qn("w:val"), ""), re.I)
        if hit:
            return min(4, int(hit.group(1)) or 1)
    ind = el.find("w:pPr/w:ind", el.nsmap)
    if ind is not None:
        try:
            left = int(ind.get(qn("w:left")) or 0)
        except ValueError:
            left = 0
        try:
            chars = int(ind.get(qn("w:leftChars")) or 0)
        except ValueError:
            chars = 0
        step = chars // 100 if chars >= 100 else (left // 400 if left >= 300 else 0)
        if step >= 1:
            return min(4, step + 1)
    return 1


def _read_toc_entries(block, doc=None) -> list[dict]:
    from api.services.tenders.pdf_format import _TOC_PIECE, _toc_row_label

    rows = []
    from api.services.tenders.word_structure import numbering_labels

    numbers = numbering_labels(doc, block) if doc is not None else {}
    for el in block[1:]:
        if el.tag != qn("w:p"):
            continue
        lines = Paragraph(el, None).text.splitlines() or [_text(el)]
        for raw in lines:
            pieces = _TOC_PIECE.findall(raw)
            pieces = pieces if len(pieces) > 1 else [raw]
            for piece in pieces:
                if re.fullmatch(r"\s*[-－]?\d{1,4}[-－]?\s*", piece):
                    continue
                title = _toc_row_label(el) if len(lines) == 1 and len(pieces) == 1 else None
                title = title or _toc_visible_title(piece)
                if not title or title in {"目录", "目次"} or len(title) > 70 or "。" in title:
                    continue
                level = _toc_para_level(el, title, doc)
                if el in numbers and len(lines) == 1 and len(pieces) == 1:
                    prefix, level = numbers[el]
                    if not re.match(r"^(?:[一二三四五六七八九十]+、|[（(]|\d+[.、])", title):
                        title = prefix + title
                rows.append({"title": title, "level": level})
    return rows


_RESPONSE_TOC_MARKS = (
    "资格资信",
    "合同条款响应",
    "差异表",
    "偏差表",
    "响应方案",
    "分项报价",
)


def _is_format_chapter_toc(rows: list[dict], brief=None) -> bool:
    """原版格式章目录。施工组织那套，或响应文件这种五条编号目录，都按原顺序。"""
    if len(rows) >= 6:
        hits = 0
        for row in rows:
            name = _key(row["title"])
            if any(mark in name for mark in _FORMAT_TOC_MARKS):
                hits += 1
        if hits >= 6 and hits * 2 >= len(rows):
            return True
    if len(rows) < 3:
        return False
    named = 0
    numbered = 0
    for row in rows:
        name = _key(row["title"])
        title = re.sub(r"\s+", "", row["title"])
        if any(mark in name for mark in _RESPONSE_TOC_MARKS):
            named += 1
        if re.match(r"^[一二三四五六七八九十]{1,3}、", title):
            numbered += 1
    if named >= 3 and named * 2 >= len(rows):
        return True
    if brief is not None and (getattr(brief, "layoutMode", "") or "").strip() == "outline":
        from api.services.tenders.slice_rules import is_format_start

        if is_format_start(getattr(brief, "outlineChapter", "") or ""):
            return numbered >= 3 and numbered * 2 >= len(rows)
    return False


def _anchor_keys(title: str) -> list[str]:
    from api.services.tenders.pdf_format import _title_keys

    keys: list[str] = []
    plain = re.sub(r'[“”"‘’]', "", title)
    for src in (title, _key(title), plain):
        for key in _title_keys(src):
            if key and key not in keys:
                keys.append(key)
    if "投标函及投标函附录" in plain:
        keys.append("投标函")
    return keys


def _source_toc_rows(brief, current):
    """Read the immutable source format, never a previously rebuilt output TOC."""
    from api.services.tenders.pdf_format import _word_format_file

    if _native_chapters(brief) and brief.invitationId:
        path = _word_format_file(brief.invitationId)
        if path is not None:
            source = Document(path)
            rows = _read_toc_entries(_body_toc_block(source, brief), source)
            if rows:
                return rows
    return current


def _find_body_anchor(children, cursor: int, title: str, block: list, *, skip=None):
    from api.services.tenders.pdf_format import _toc_heading_hit

    keys = _anchor_keys(title)
    want = _key(title)
    candidates = []
    for el in children[cursor:]:
        if el in block or "PAGEREF" in el.xml:
            continue
        paras = (
            [el]
            if el.tag == qn("w:p")
            else (list(el.iter(qn("w:p"))) if el.tag == qn("w:tbl") else [])
        )
        for p in paras:
            if skip and p in skip:
                continue
            # Converted formats often put parent/child headings in one paragraph.
            # Tabs and line breaks separate headings; they do not create TOC rows here.
            for text in re.split(r"[\n\t]+", Paragraph(p, None).text):
                text = text.strip()
                if not text or len(text) > 70:
                    continue
                compact = re.sub(r"\s+", "", text)
                candidates.append((p, text, compact))
    for match in (
        lambda text, compact: compact == re.sub(r"\s+", "", title),
        lambda text, compact: _key(text) == want,
        lambda text, compact: _toc_heading_hit(compact, keys),
    ):
        for p, text, compact in candidates:
            if match(text, compact):
                host = p
                while host is not None and host not in children:
                    host = host.getparent()
                nxt = children.index(host) + 1 if host is not None else cursor
                return p, nxt
    return None, cursor


def _format_toc_heads(doc, brief: BidBrief, block: list, rows: list[dict]) -> list[tuple]:
    from api.services.tenders.format_rules import peel_toc_lead
    from api.services.tenders.outline import classify_kind

    by_key = {}
    for item in brief.outlineItems:
        if item.skipped:
            continue
        by_key.setdefault(_key(item.title), item)
        if item.rawTitle:
            by_key.setdefault(_key(item.rawTitle), item)
    merged = []
    for i, row in enumerate(rows):
        key = _key(row["title"])
        if not key:
            continue
        src = by_key.get(key)
        level = row["level"] or 1
        raw = row["title"] or (src.rawTitle if src is not None else "")
        if src is not None:
            item = src.model_copy(
                update={"level": level, "tocEligible": True, "rawTitle": raw or src.rawTitle}
            )
        else:
            bare = peel_toc_lead(row["title"]) or row["title"]
            item = OutlineItem(
                id=f"fmt-toc-{i + 1:02d}",
                title=bare,
                rawTitle=raw or bare,
                level=level,
                tocEligible=True,
                kind=classify_kind(bare) or "unknown",
            )
        merged.append(item)
    # 目录里没有的子项留在原章节里，不接到目录末尾改顺序
    if merged:
        brief.outlineItems = _sync_format_outline(brief, merged)
    children = list(doc.element.body)
    heads = []
    used = {}
    for item in merged:
        # Search independently: an incomplete/misordered old TOC must not hide
        # an earlier physical chapter or borrow the previous chapter's bookmark.
        start = children.index(block[-1]) + 1 if block else 0
        key = _key(item.rawTitle or item.title)
        anchor, _ = _find_body_anchor(
            children, start, item.rawTitle or item.title, block, skip=used.get(key)
        )
        if anchor is None:
            continue
        used.setdefault(key, set()).add(anchor)
        heads.append((anchor, anchor, item))
    return heads


def _sync_format_outline(brief: BidBrief, items: list[OutlineItem]) -> list[OutlineItem]:
    """目录条目写回大纲，已有草稿和跳过状态留着。"""
    pool = list(brief.outlineItems)
    used: set[int] = set()
    ordered = []
    for item in items:
        key = _key(item.rawTitle or item.title)
        old = next(
            (
                cur
                for cur in pool
                if id(cur) not in used
                and (_key(cur.title) == key or (cur.rawTitle and _key(cur.rawTitle) == key))
            ),
            None,
        )
        if old is None:
            ordered.append(item)
            continue
        used.add(id(old))
        ordered.append(
            old.model_copy(
                update={
                    "level": item.level or old.level,
                    "tocEligible": True,
                    "rawTitle": item.rawTitle or old.rawTitle or old.title,
                }
            )
        )
    ordered.extend(cur for cur in pool if id(cur) not in used)
    return ordered


def ensure_toc_rows(doc, brief: BidBrief, *, volume: str = "") -> None:
    """Rebuild only chapter rows, with stable real-body anchors and numbered levels."""
    from api.services.tenders.assemble import _fill_toc_pages
    from api.services.tenders.document import (
        _apply_toc_indent,
        _bookmark_paragraph,
        _subset_bookmarks,
        _write_toc_line,
    )
    from api.services.tenders.outline import subset_toc_titles
    from api.services.tenders.pdf_format import _styled_p

    block = _body_toc_block(doc, brief)
    _restore_form_body_titles(doc, brief, block)
    sections = body_sections(doc, brief)
    # 落款行可能和大纲标题撞车，目录锚点从它后面的正文章节起
    placed = [row for row in sections if not _cover_signoff(_text(row[1]))] or sections
    original = _source_toc_rows(brief, _read_toc_entries(block, doc))
    authoritative = _native_chapters(brief) and bool(original)
    format_toc = authoritative or _is_format_chapter_toc(original, brief)
    if format_toc:
        heads = _format_toc_heads(doc, brief, block, original)
    else:
        heads = body_headings(doc, brief)
    if authoritative and not heads and volume in {"business", "technical"}:
        from api.services.tenders.outline import classify_kind

        # Some invitations have no original directory for this volume at all.
        # Keep the existing built-in supplement path usable in that case only.
        applicable = any(
            section_volume(OutlineItem(title=row["title"], kind=classify_kind(row["title"])))
            == volume
            for row in original
        )
        if not applicable:
            authoritative = False
            heads = body_headings(doc, brief)
    if _native_chapters(brief) and not authoritative:
        # Keep original labels, supplement omitted chapters from the physical
        # body, then use that same order for both TOC entries and page targets.
        by_anchor = {p: (el, p, item) for el, p, item in heads}
        for el, p, item in body_headings(doc, brief):
            if _physical_chapter(p, doc):
                by_anchor.setdefault(p, (el, p, item))
        positions = {el: n for n, el in enumerate(doc.element.body.iter())}
        heads = sorted(by_anchor.values(), key=lambda row: positions[row[1]])
        format_toc = True
    signoffs = _front_signoffs(doc)
    if block:
        _park_toc_after_cover(block[0], signoffs)
        _drop_breaks_inside_cover(block[0], signoffs)
        for el in signoffs:
            _clear_page_break_before(el)
        # Cover repair can already remove page-break paragraphs from this wider
        # cross-page directory snapshot. Never try to remove detached nodes twice.
        block = [el for el in block if el.getparent() is not None]
    if not heads:
        # A forms-only volume still retains every form in the body. It has no
        # chapter directory; never advertise arbitrary form labels as chapters.
        for el in block:
            if el.find("w:pPr/w:sectPr", el.nsmap) is None:
                el.getparent().remove(el)
        if placed:
            Paragraph(placed[0][1], doc).paragraph_format.page_break_before = True
            _collapse_toc_page_breaks(placed[0][0])
        return
    if block:
        title = block[0]
        # 【修改2：统一删除所有旧目录条目】
        # 删除旧目录条目时覆盖整个目录范围（全部条目，含前6条），
        # 不保留任何 numPr 旧条目。同时清除旧的 numPr 编号信息。
        for el in block[1:]:
            if _cover_signoff(_text(el)):
                continue
            # 清除旧的 numPr 编号信息，避免残留
            num_pr = el.find("w:pPr/w:numPr", el.nsmap)
            if num_pr is not None:
                p_pr = el.find("w:pPr", el.nsmap)
                if p_pr is not None:
                    p_pr.remove(num_pr)
            if el.find("w:pPr/w:sectPr", el.nsmap) is None:
                el.getparent().remove(el)
            else:
                for child in list(el):
                    if child.tag != qn("w:pPr"):
                        el.remove(child)
    else:
        title = _styled_p("目录", size_pt=18, bold=True, center=True)
        (placed[0][0] if placed else heads[0][0]).addprevious(title)
        Paragraph(title, doc).paragraph_format.page_break_before = True

    # 保存目录标题元素，避免被后续循环变量覆盖
    toc_title_el = title

    for child in list(toc_title_el):
        if child.tag == qn("w:r") and (
            next(child.iter(qn("w:fldChar")), None) is not None
            or next(child.iter(qn("w:instrText")), None) is not None
        ):
            toc_title_el.remove(child)
    if toc_title_el.getprevious() is not None:
        Paragraph(toc_title_el, doc).paragraph_format.page_break_before = True

    # Use only TOC-target bookmarks when choosing the body page-number origin.
    # The first true chapter need not be the first body page (forms can precede it).
    # 格式章目录以第一条正文章节为页码起点，不拿大纲里剩下的那一节当起点。
    first_body = heads[0][1] if format_toc else (placed[0][1] if placed else heads[0][1])
    origin = "toc_native_body_start"
    if not any(n.get(qn("w:name")) == origin for n in first_body.iter(qn("w:bookmarkStart"))):
        # Move an old origin left behind by a previously incomplete directory.
        for node in list(doc.element.body.iter(qn("w:bookmarkStart"))):
            if node.get(qn("w:name")) == origin:
                ident = node.get(qn("w:id"))
                node.getparent().remove(node)
                for end in list(doc.element.body.iter(qn("w:bookmarkEnd"))):
                    if end.get(qn("w:id")) == ident:
                        end.getparent().remove(end)
        _bookmark_paragraph(Paragraph(first_body, doc), origin)
    if not _cover_signoff(_text(first_body)):
        Paragraph(first_body, doc).paragraph_format.page_break_before = True
    for el in signoffs:
        _clear_page_break_before(el)

    items = []
    stack = []
    for _, _, item in heads:
        rank = max(1, min(4, item.level))
        while stack and stack[-1] >= rank:
            stack.pop()
        stack.append(rank)
        items.append(item if authoritative else item.model_copy(update={"level": len(stack)}))
    from api.services.tenders.format_rules import peel_toc_lead

    # 【修改1：彻底剥掉所有编号，只留纯标题文字】
    # 移除文本编号（一、/（一）/第X部分）和任何前导编号
    numbered_items = [
        item.model_copy(
            update={
                "rawTitle": re.sub(
                    r"^第[一二三四五六七八九十百0-9]+部分[、.．]?",
                    "",
                    peel_toc_lead(item.rawTitle or item.title),
                ),
            }
        )
        for item in items
    ]
    titles = subset_toc_titles(numbered_items)
    if format_toc:
        # The source labels already include literal or resolved Word numbering.
        # Filtering a volume must not renumber its surviving original entries.
        titles = [item.rawTitle or _text(p) for _, p, item in heads]

    anchor = toc_title_el
    rows = []
    used_bookmarks = set()
    for p in {p for _, p, _ in heads}:
        for field in list(p.findall(qn("w:fldSimple"))):
            if "\\f N " in field.get(qn("w:instr"), ""):
                p.remove(field)

    # 重新计数，准备写入目录行
    from api.services.tenders.format_rules import cn_ordinal

    level1_counter = 0
    level2_counters = {}
    current_level1_idx = -1

    for idx, ((_, p, item), normalized, shown) in enumerate(zip(heads, items, titles, strict=True)):
        # 统一编号逻辑
        if format_toc:
            toc_label = ""
        elif normalized.level == 1:
            level1_counter += 1
            current_level1_idx = idx
            level2_counters[current_level1_idx] = 0
            # 为 level=1 主条目添加编号：一、二、三...
            toc_label = f"{cn_ordinal(level1_counter)}、"
        elif normalized.level == 2:
            if current_level1_idx >= 0:
                level2_counters[current_level1_idx] += 1
                # 为 level=2 子条目添加编号：（一）（二）（三）
                toc_label = f"（{cn_ordinal(level2_counters[current_level1_idx])}）"
            else:
                toc_label = ""
        else:
            toc_label = ""

        # Prefer our existing anchor to avoid increasing bookmark counts on a
        # repeated normalization; original author bookmarks remain untouched.
        stem = "toc_" + re.sub(r"[^\w]", "_", "chapter_" + item.id)[:30]
        candidates = [
            n.get(qn("w:name"))
            for n in p.iter(qn("w:bookmarkStart"))
            if n.get(qn("w:name"), "").startswith("toc_chapter_")
            and n.get(qn("w:name")) not in used_bookmarks
        ]
        old = next(
            iter(
                sorted(
                    candidates,
                    key=lambda name: (name != stem, not name.startswith(stem + "_"), name),
                )
            ),
            None,
        )
        bookmark = (
            old
            or _subset_bookmarks(
                doc,
                [
                    item.model_copy(
                        update={"id": "chapter_" + item.id},
                    )
                ],
            )[0]
        )
        if old is None:
            _bookmark_paragraph(Paragraph(p, doc), bookmark)
        used_bookmarks.add(bookmark)
        # A controlled TC family keeps Word's own TOC refresh from recollecting
        # every Heading-style form/table in the original invitation.
        field = OxmlElement("w:fldSimple")
        safe_title = shown.replace('"', "＂")
        field.set(qn("w:instr"), f' TC "{safe_title}" \\f N \\l {normalized.level} ')
        p.append(field)
        entry = OxmlElement("w:p")
        para = Paragraph(entry, doc)
        # 使用统一的编号标签
        _write_toc_line(para, 0, shown, bookmark, "1", label=toc_label, level=normalized.level)
        _apply_toc_indent(para, normalized.level)
        anchor.addnext(entry)
        anchor = entry
        rows.append(entry)
    # A converted paragraph may own several headings/bookmark ends. Keep all
    # controlled TC fields after those ends on both first and repeated passes.
    for p in {p for _, p, _ in heads}:
        fields = [
            field
            for field in p.findall(qn("w:fldSimple"))
            if "\\f N " in field.get(qn("w:instr"), "")
        ]
        for field in fields:
            p.remove(field)
            p.append(field)
    # Wrap the cached rows in a TOC driven solely by the above TC entries.
    for host, kind in (
        (toc_title_el, "begin"),
        (toc_title_el, "instruction"),
        (toc_title_el, "separate"),
        (rows[-1], "end"),
    ):
        run = OxmlElement("w:r")
        node = OxmlElement("w:instrText" if kind == "instruction" else "w:fldChar")
        if kind == "instruction":
            node.text = " TOC \\f N \\h \\z "
            node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        else:
            node.set(qn("w:fldCharType"), kind)
        run.append(node)
        host.append(run)
    _collapse_toc_page_breaks(toc_title_el)
    _collapse_toc_page_breaks(first_body)
    _clean_toc_gap(toc_title_el, first_body, rows)
    _fill_toc_pages(doc, brief.documentFormat)


def _clean_toc_gap(title, first_body, rows) -> None:
    """Check the rebuilt TOC-to-body gap and remove any surviving old cache rows."""
    from api.services.tenders.pdf_format import _is_toc_heading, _toc_row_label

    body = title.getparent()
    host = first_body
    while host is not None and host.getparent() is not body:
        host = host.getparent()
    if host is None:
        return
    region = list(body)[body.index(title) + 1 : body.index(host)]
    for el in region:
        if el in rows or el.tag != qn("w:p"):
            continue
        if _is_toc_heading(_text(el)) or _toc_row_label(el) is not None:
            if el.find("w:pPr/w:sectPr", el.nsmap) is not None:
                # The section properties still own header/footer relationships.
                for child in list(el):
                    if child.tag != qn("w:pPr"):
                        el.remove(child)
            else:
                body.remove(el)


def _validation_body_start(doc, children, rows, first_target: int) -> int:
    """Recognize an original, unlisted form before the first TOC target, read-only."""
    from api.services.tenders.pdf_format import _toc_row_label, toc_block_ids
    from api.services.tenders.word_structure import outline_level

    block = set(toc_block_ids(doc))
    listed = {_key(row.text.split("\t", 1)[0]) for row in rows}
    last_row = max(children.index(row._p) for row in rows)
    generated = ("toc_", "scan_slot_", "material_", "biz_", "full_draft_", "weitai_")
    for n in range(last_row + 1, first_target):
        node = children[n]
        if (
            node.tag != qn("w:p")
            or node in block
            or _toc_row_label(node) is not None
            or not outline_level(doc, node)
            or not _physical_chapter(node, doc)
            # A listed title with a later target is a broken anchor, not an
            # unlisted form. Keep rejecting it, including equivalent aliases.
            or _key(_text(node)) in listed
        ):
            continue
        if not any(
            (name := marker.get(qn("w:name"), ""))
            and not name.startswith(generated)
            for marker in node.iter(qn("w:bookmarkStart"))
        ):
            continue
        for following in children[n + 1 : first_target]:
            if following.tag == qn("w:tbl"):
                if sum(bool(_text(cell)) for cell in following.iter(qn("w:tc"))) > 1:
                    return n
            elif following.tag == qn("w:p"):
                text = _text(following)
                if (
                    len(text) > 12
                    and re.search(r"[。；]", text)
                    and not _directory_entry(following)
                    and not re.search(r"粘贴|扫描件|复印件|【待补充】", text)
                ):
                    return n
    return first_target


def validate_native_toc(doc) -> dict:
    """Validate the final DOCX's complete TOC-to-body interval, not just a row list."""
    from api.services.tenders.document import _pageref_bookmark
    from api.services.tenders.pdf_format import _is_toc_heading
    from common.errors import AppError, ErrorCode

    children = list(doc.element.body)
    titles = [p for p in doc.paragraphs if _is_toc_heading(p.text)]
    rows = [p for p in doc.paragraphs if _pageref_bookmark(p)]
    if not titles and not rows:
        return {"entries": 0}
    names = {}
    for n, child in enumerate(children):
        for bookmark in child.iter(qn("w:bookmarkStart")):
            names.setdefault(bookmark.get(qn("w:name")), []).append(n)
    targets = []
    problems = []
    for row in rows:
        hits = names.get(_pageref_bookmark(row), [])
        if len(hits) != 1:
            problems.append("目录书签缺失或重复")
        else:
            targets.append(hits[0])
    if len(titles) != 1 or not rows or not targets:
        problems.append("目录标题或条目数量异常")
    else:
        start = children.index(titles[0]._p)
        end = min(targets)
        if end <= start or any(children.index(row._p) >= end for row in rows):
            problems.append("目录页码指向目录自身")
        else:
            # Some original formats start with an unlisted form/table. The
            # first PAGEREF target alone does not define where their body starts.
            end = _validation_body_start(doc, children, rows, end)
        allowed = {row._p for row in rows}
        for node in children[start + 1 : end]:
            text = _text(node)
            if next(node.iter(qn("w:drawing")), None) is not None or node.tag == qn("w:tbl"):
                problems.append("目录区混入附件或表格")
                break
            if node not in allowed and text.strip():
                problems.append("目录区残留非页码目录条目或占位文字：" + text[:45])
                break
    if problems:
        raise AppError(
            ErrorCode.VALIDATION,
            "目录结构校验失败：" + "；".join(dict.fromkeys(problems)),
            status_code=422,
        )
    return {"entries": len(rows), "firstBodyBlock": end}


def _restore_form_body_titles(doc, brief: BidBrief, block: list) -> None:
    """Legacy outline renderers used TOC labels as the only visible form titles."""
    from api.services.tenders.document import _pageref_bookmark
    from api.services.tenders.outline import is_outline_chapter
    from api.services.tenders.pdf_format import _styled_p

    present = {_key(item.title) for _, _, item in body_sections(doc, brief)}
    present.update(
        _key(line.strip())
        for para in doc.paragraphs
        if para._p not in block
        for line in re.split(r"[\n\t]+", para.text)
        if line.strip()
    )
    refs = {
        _key(Paragraph(el, doc).text.split("\t", 1)[0]): _pageref_bookmark(Paragraph(el, doc))
        for el in block[1:]
        if el.tag == qn("w:p")
    }
    for item in brief.outlineItems:
        key = _key(item.title)
        if item.skipped or is_outline_chapter(item) or key in present:
            continue
        bookmark = refs.get(key)
        if not bookmark:
            continue
        target = next(
            (
                p
                for p in doc.paragraphs
                if p._p not in block
                and any(n.get(qn("w:name")) == bookmark for n in p._p.iter(qn("w:bookmarkStart")))
            ),
            None,
        )
        if target is not None:
            target._p.addprevious(
                _styled_p(item.rawTitle or item.title, size_pt=14, bold=True, center=True)
            )
            present.add(key)


def _collapse_toc_page_breaks(anchor) -> None:
    """Do not combine a leading pageBreakBefore with an adjacent bare page break."""
    cursor = anchor.getprevious()
    while cursor is not None and cursor.tag == qn("w:p") and not _text(cursor):
        previous = cursor.getprevious()
        if cursor.find("w:pPr/w:sectPr", cursor.nsmap) is not None:
            # The original section already starts the following page.
            ppr = anchor.find(qn("w:pPr"))
            before = ppr.find(qn("w:pageBreakBefore")) if ppr is not None else None
            if before is not None:
                ppr.remove(before)
            break
        if next(cursor.iter(qn("w:drawing")), None) is None and any(
            n.get(qn("w:type")) == "page" for n in cursor.iter(qn("w:br"))
        ):
            cursor.getparent().remove(cursor)
        cursor = previous


_COVER_LINE = (
    re.compile(r"^(?:投标人|供应商)(?:名称)?[：:]?.{0,24}(?:加盖公章|盖单位章|盖章)[)）]?[：:]?$"),
    re.compile(
        r"^(?:法定代表人|法人代表)(?:或(?:其)?(?:委托|授权)代理人)?[：:]?.{0,24}"
        r"(?:签字或盖章|签字|签章|盖章)[)）]?[：:]?(?:年月|年[_＿]*月[_＿]*日)?$"
    ),
    re.compile(r"^(?:日期[：:])?[_＿]*年[_＿]*月[_＿]*日$"),
    re.compile(r"^年月日$"),
    re.compile(r"^日期[：:]?$"),
    re.compile(r"^(?:日期[：:])?\d{4}年\d{1,2}月\d{1,2}日$"),
)


def _cover_signoff(text: str) -> bool:
    n = re.sub(r"\s+", "", text or "")
    if not n or len(n) > 40:
        return False
    return any(pat.fullmatch(n) for pat in _COVER_LINE)


def fill_native_cover(doc, brief: BidBrief) -> None:
    """Fill cover fields within existing runs, retaining their fonts and paragraph XML."""
    from api.services.tenders.document import _ymd
    from api.services.tenders.pdf_format import _fill_hints, _replace_span, _swap_tokens

    children = _cover_elements(doc, brief)
    if not children:
        return
    number = brief.tenderNo.strip()
    if not number:
        from api.services.tenders.format_rules import extract_tender_no
        from api.services.tenders.requirement_bid import invitation_text

        number = extract_tender_no(invitation_text(brief))
    cover_text = "".join(_text(el) for el in children)
    if not any(word in cover_text for word in ("招标编号", "项目编号", "采购编号")):
        host = next(
            (
                el
                for el in children
                if el.tag == qn("w:p") and any(word in _text(el) for word in ("投标人", "供应商"))
            ),
            None,
        )
        if host is not None:
            number_line = deepcopy(host)
            for node in list(number_line):
                if node.tag != qn("w:pPr"):
                    number_line.remove(node)
            para = Paragraph(number_line, doc)
            run = para.add_run(f"项目编号：{number or '【待补充】'}")
            sample = host.find("w:r/w:rPr", host.nsmap)
            if sample is not None:
                run._r.insert(0, deepcopy(sample))
            host.addprevious(number_line)
            children.insert(children.index(host), number_line)
    values = (
        ("项目名称", brief.projectName),
        ("招标编号", number),
        ("项目编号", number),
        ("采购编号", number),
        ("投标人", brief.bidderName),
        ("供应商", brief.bidderName),
        ("法定代表人", brief.legalPersonName),
        ("法人代表", brief.legalPersonName),
    )
    for el in children:
        for p in el.iter(qn("w:p")):
            _fill_hints(p, brief)
            _swap_tokens(p, brief)
            compact = re.sub(r"\s+", "", _text(p))
            if (
                compact in {"日期", "日期：", "日期:"}
                or compact == "年月日"
                or re.fullmatch(r"(?:日期[:：]?)?[_＿\s]*年[_＿\s]*月[_＿\s]*日", _text(p))
            ):
                year, month, day = _ymd(brief.bidDate)
                shown = f"{year}年{month}月{day}日"
                if compact.startswith("日期"):
                    shown = "日期：" + shown
                _replace_span(p, _text(p), shown)
                continue
            for label, value in values:
                if not value or not compact.startswith(label) or value in compact:
                    continue
                # Only blank slots, never replace already populated source facts.
                extra = ""
                if label in {"法定代表人", "法人代表"}:
                    extra = r"(?:或(?:其)?(?:委托|授权)代理人)?"
                match = re.fullmatch(
                    re.escape(label)
                    + r"(?:名称)?"
                    + extra
                    + r"[：:]?([_＿]*)"
                    + r"([（(](?:加盖公章|盖单位章|盖章|签字或盖章|签字|签章)[)）])?"
                    + r"(年月|年[_＿]*月[_＿]*日)?",
                    compact,
                )
                if match:
                    nodes = list(p.iter(qn("w:t")))
                    if nodes:
                        suffix = match.group(2) or ""
                        tail = match.group(3) or ""
                        text = "".join(n.text or "" for n in nodes)
                        blank = re.search(r"[_＿　 \t]+(?=[（(]|$)", text)
                        if blank:
                            _replace_span(p, blank.group(), value)
                        elif suffix and suffix in text:
                            _replace_span(p, suffix, value + suffix)
                        else:
                            nodes[-1].text = (nodes[-1].text or "").rstrip("_＿　 ") + value
                        if tail and tail in "".join(n.text or "" for n in p.iter(qn("w:t"))):
                            year, month, day = _ymd(brief.bidDate)
                            _replace_span(p, tail, f"{year}年{month}月{day}日")
                            for tab in list(p.iter(qn("w:tab"))):
                                holder = tab.getparent()
                                if holder is not None:
                                    holder.remove(tab)
                    break
