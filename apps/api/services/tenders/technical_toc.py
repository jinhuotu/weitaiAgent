"""Technical-volume TOC: one structural field, one ordered body projection."""
from copy import deepcopy
import re

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph


def _instruction(node):
    return ' '.join([*(n.text or '' for n in node.iter(qn('w:instrText'))),
                     *(n.get(qn('w:instr'), '') for n in node.iter(qn('w:fldSimple')))])


# 括号编号（（一）/（1））是子项条目的通用形态，用于给没有大纲级别的段落定级。
_PAREN_PREFIX = re.compile(r'^\s*[（(][一二三四五六七八九十百零〇0-9]+[）)]')


def _is_toc(node):
    instructions = [*(n.text or '' for n in node.iter(qn('w:instrText'))),
                    *(n.get(qn('w:instr'), '') for n in node.iter(qn('w:fldSimple')))]
    return any(re.match(r'^\s*TOC\b', value) for value in instructions)


def _regions(doc):
    """Top-level field hosts, including SDTs and fields spanning paragraphs."""
    children = list(doc.element.body)
    regions = []
    active = None
    depth = 0
    for i, node in enumerate(children):
        if active is None and _is_toc(node):
            active = i
            # The instruction can follow its begin in an earlier paragraph.
            if not any(n.get(qn('w:fldCharType')) == 'begin' for n in node.iter(qn('w:fldChar'))):
                if i and any(n.get(qn('w:fldCharType')) == 'begin' for n in children[i-1].iter(qn('w:fldChar'))):
                    active = i-1
                    depth = 1
        if active is not None:
            for field in node.iter(qn('w:fldChar')):
                kind = field.get(qn('w:fldCharType'))
                depth += (kind == 'begin') - (kind == 'end')
            if depth <= 0:
                regions.append(children[active:i+1])
                active = None
                depth = 0
    if active is not None:
        # A malformed field must never consume the rest of the body.
        regions.append([children[active]])
    covered = {n for region in regions for host in region for n in host.iter()}
    # Earlier technical synchronizers appended cached rows *after* the TOC end.
    # Treat these as residual cache, never as body headings or extra directories.
    for node in children:
        if (node not in covered and node.tag == qn('w:p')
                and re.search(r'\bPAGEREF\b', _instruction(node))
                and list(node.iter(qn('w:tab')))
                and not list(node.iter(qn('w:drawing')))):
            regions.append([node])
    return regions


def _level(doc, node):
    """Explicit or inherited outline level, without language-specific style names."""
    prop = node.find(qn('w:pPr'))
    style_id = prop.find(qn('w:pStyle')) if prop is not None else None
    style = next((s for s in doc.styles if style_id is not None and s.style_id == style_id.get(qn('w:val'))), None)
    props = [prop]
    names = []
    visited = set()
    while style is not None and style.style_id not in visited:
        visited.add(style.style_id)
        names.append(style.name or '')
        props.append(style.element.find(qn('w:pPr')))
        style = style.base_style
    for p in props:
        n = p.find(qn('w:outlineLvl')) if p is not None else None
        if n is not None:
            return int(n.get(qn('w:val'))) + 1 if int(n.get(qn('w:val'))) < 9 else None
    # 部分模板的 Heading 样式不带 outlineLvl，只按大纲级别判断会把整个章标题漏掉。
    # 样式名是文档里显式声明的稳定依据，这里作为兜底，避免目录少章。
    for name in names:
        matched = re.match(r'\s*(?:Heading|标题)\s*(\d+)\s*$', name)
        if matched:
            value = int(matched.group(1))
            return value if value < 9 else None
    return None


def _targets(region):
    return re.findall(r'\bPAGEREF\s+([^\s\\]+)', ' '.join(_instruction(n) for n in region))


def _remove(doc, node):
    """Retain section transitions with different geometry/header configuration."""
    section = node.find('.//' + qn('w:sectPr'))
    if section is not None:
        # Compare with the next section by physical XML order (not source lines).
        sections = list(doc.element.body.iter(qn('w:sectPr')))
        index = sections.index(section)
        following = sections[index+1] if index+1 < len(sections) else None
        def signature(s):
            return [(e.tag, sorted(e.attrib.items())) for e in s.iter()
                    if e.tag not in {qn('w:type'), qn('w:sectPr')}] if s is not None else None
        if signature(section) != signature(following):
            spacer = OxmlElement('w:p')
            props = OxmlElement('w:pPr')
            props.append(deepcopy(section))
            spacer.append(props)
            node.addprevious(spacer)
    node.getparent().remove(node)


def sync(doc, brief):
    from api.services.tenders.document import _bookmark_paragraph, _pageref_bookmark, _write_toc_line
    from api.services.tenders.technical_render import _package_display_titles, _clean, anonymous_text

    regions = _regions(doc)
    if not regions:
        # Legacy generated documents can have PAGEREF caches but no enclosing TOC.
        from api.services.tenders.pdf_format import toc_block_ids
        legacy = toc_block_ids(doc)
        if legacy:
            regions = [legacy]
    excluded = {n for r in regions for host in r for n in host.iter()}
    targets = {t for region in regions for t in _targets(region)}
    package = brief.technicalPackage or {}
    child_titles = {p['title'] for p in package.get('sections', [])}
    appendix_titles = {p['title'] for p in [*package.get('tables', []), *package.get('figures', [])]}
    candidates = []
    display = {}
    if package.get('template_kind') == 'chapter_heading':
        chapter_titles, point_titles = _package_display_titles(package)
        anchors = {mark.get(qn('w:name')): mark.getparent()
                   for mark in doc.element.body.iter(qn('w:bookmarkStart'))}
        mismatched = []
        for index, chapter in enumerate(package['chapters']):
            name = chapter['title']
            entries = [(f'toc_technical_chapter_{index}', 1, chapter_titles[name])]
            entries.extend(('toc_technical_'+point['id'], 2, point_titles[point['id']])
                           for point in package['sections'] if point.get('chapter') == name)
            for bookmark, level, title in entries:
                node = anchors.get(bookmark)
                if node is None:
                    # 正文缺这条书签只跳过这一条，不放弃整次重建（否则整本目录
                    # 会退回「章级行无域」的旧形态，并让下游校验报 422）。
                    mismatched.append('missing:' + bookmark)
                    continue
                paragraph = Paragraph(node, doc)
                title = _clean(title, brief)
                if paragraph.text != title or paragraph.style.name != f'Heading {level}':
                    mismatched.append('title:' + title[:20])
                    continue
                candidates.append((paragraph, level))
                display[node] = (bookmark, title)
        # 只要有一条对不上，就丢弃这条精确映射，改由下面的结构扫描重建
        # （判据是「映射是否完整成立」，不认任何具体章名或章节数）。
        if mismatched:
            candidates = []
            display = {}
    from api.services.tenders.outline import _strip_outline_prefix

    chapter_titles = {
        _strip_outline_prefix(_clean(ch.get('title', ''), brief)).strip()
        for ch in package.get('chapters', [])
    } - {''}
    for p in doc.paragraphs:
        if display:
            break
        if p._p in excluded or not p.text.strip():
            continue
        marks = {m.get(qn('w:name')) for m in p._p.iter(qn('w:bookmarkStart'))}
        level = _level(doc, p._p)
        text_now = p.text.strip()
        # Target bookmarks are body evidence; source point/appendix metadata is
        # used only to recognize existing headings, never to create body text.
        # 评分表里的章标题（去掉「一、」「第一章」等前缀后）命中正文标题时按一级处理，
        # 避免章级目录行因样式不规范被整章漏掉。
        if level is None and not (marks & targets or text_now in child_titles | appendix_titles):
            if chapter_titles and _strip_outline_prefix(text_now).strip() in chapter_titles:
                level = 1
            else:
                continue
        if level is None:
            from api.services.tenders.word_structure import numbering_info
            numbering = numbering_info(doc, p._p)
            if numbering is not None:
                level = numbering[1] + 1
        if level is None:
            # 评分表子项、括号编号段落都按二级；其余才视为章级，
            # 避免没有大纲级别的残留段落被当成章标题插进目录。
            level = 2 if (text_now in child_titles | appendix_titles
                          or _PAREN_PREFIX.match(text_now)) else 1
        candidates.append((p, level))
    if not candidates:
        return False
    from api.services.tenders.word_structure import numbering_labels
    labels = {} if display else numbering_labels(doc, [p._p for p in doc.paragraphs if p._p not in excluded])
    # 目录重建时沿用旧目录已算好的页码，避免重跑一次就把页码打回占位值。
    # 只按「去掉序号前缀后的标题文本」比对，不认任何具体章节名。
    page_cache = {}
    for para in doc.paragraphs:
        if not _pageref_bookmark(para):
            continue
        text = (para.text or '').strip()
        title_part, _, page_part = text.rpartition('\t')
        key = _strip_outline_prefix(title_part).strip()
        if title_part and key and page_part.strip().isdigit():
            page_cache.setdefault(key, page_part.strip())
    # Cover headings preceding every directory are not body chapters.
    if regions and not display:
        body = doc.element.body
        first = min(body.index(r[0]) for r in regions)
        candidates = [(p,l) for p,l in candidates if body.index(p._p) > first]
    if not candidates:
        return False
    # 目录条目去重：正文里偶尔残留与标题同名的段落（旧目录行、重复标题），
    # 它们会被识别成第二条目录。同一章内同名条目只保留文档顺序第一条，
    # 章级按标题全局去重，避免目录出现重复项。
    seen_keys = set()
    chapter_key = ''
    deduped = []
    for p, level in candidates:
        plain = _strip_outline_prefix((p.text or '').strip())
        key = ('c', plain) if level <= 1 else ('s', chapter_key, plain)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if level <= 1:
            chapter_key = plain
        deduped.append((p, level))
    candidates = deduped
    bookmarks = {m.get(qn('w:name')) for p,_ in candidates for m in p._p.iter(qn('w:bookmarkStart'))}
    selected = max(regions, key=lambda r: len(set(_targets(r)) & bookmarks)) if regions else None
    title = selected[0] if (selected and selected[0].tag == qn('w:p')
            and not list(selected[0].iter(qn('w:fldSimple')))
            and 'PAGEREF' not in _instruction(selected[0])) else None
    if title is None:
        title = OxmlElement('w:p')
        (selected[0] if selected else candidates[0][0]._p).addprevious(title)
    # Strip only generated TC fields, leaving unrelated fields/body runs intact.
    for p in doc.paragraphs:
        for field in list(p._p.iter(qn('w:fldSimple'))):
            if re.search(r'\bTC\b.*\\f\s+(?:N|T)\b', field.get(qn('w:instr'), '')):
                field.getparent().remove(field)
    for region in regions:
        for node in region:
            if node is title:
                # Preserve a standalone source title and its formatting.
                for r in list(node):
                    if r.tag != qn('w:pPr') and (list(r.iter(qn('w:fldChar'))) or list(r.iter(qn('w:instrText')))):
                        node.remove(r)
            elif node.getparent() is not None:
                _remove(doc, node)
    # 目录区清扫：原模板里手打的章级目录行常常是「标题 + 制表符 + 静态页码」，
    # 不带 PAGEREF 域，也不在任何 TOC 域区间内，上面的 region 清理覆盖不到。
    # 以目录标题为起点、以第一个正文候选段落为终点，区间内的非空段落都属于目录，
    # 交给下面的 rows 统一重写，避免残留行触发目录校验。
    body = doc.element.body
    start_index = None
    stop_index = None
    try:
        start_index = body.index(title)
    except ValueError:
        start_index = None
    if candidates:
        try:
            stop_index = body.index(candidates[0][0]._p)
        except ValueError:
            stop_index = None
    if start_index is not None and stop_index is not None and stop_index > start_index:
        # 区间异常长说明终点识别错了：宁可不动，让校验照常报错，也不误删正文。
        if stop_index - start_index <= 120:
            for node in list(body)[start_index + 1:stop_index]:
                if node.tag != qn('w:p'):
                    continue
                if node.find('.//' + qn('w:sectPr')) is not None:
                    continue
                if next(node.iter(qn('w:drawing')), None) is not None:
                    continue
                if not ''.join(t.text or '' for t in node.iter(qn('w:t'))).strip():
                    continue
                _remove(doc, node)
    cursor = title
    rows = []
    for i, (p, level) in enumerate(candidates):
        names = [m.get(qn('w:name')) for m in p._p.iter(qn('w:bookmarkStart'))
                 if m.get(qn('w:name'), '').startswith('toc_technical_')]
        name = names[0] if names else f'toc_technical_heading_{i}'
        if not names:
            # Avoid clashing with bookmarks elsewhere in the original document.
            all_names = {m.get(qn('w:name')) for m in doc.element.iter(qn('w:bookmarkStart'))}
            while name in all_names:
                name += '_'
            _bookmark_paragraph(p, name)
        shown = p.text.strip()
        if p._p in display:
            name, shown = display[p._p]
        label = labels.get(p._p, ('', level))[0]
        if label and not shown.startswith(label):
            shown = label + shown
        if brief.techAnonymous:
            shown = anonymous_text(shown, brief)
        tc = OxmlElement('w:fldSimple')
        escaped = shown.replace('\\', '\\\\').replace('"', '\\"')
        tc.set(qn('w:instr'), f' TC "{escaped}" \\f T \\l {min(level,9)} ')
        p._p.append(tc)
        row = OxmlElement('w:p')
        cursor.addnext(row)
        cursor = row
        page = page_cache.get(_strip_outline_prefix(shown).strip(), '1')
        _write_toc_line(Paragraph(row, doc), i, shown, name, page, label='', level=level, preserve_title=True)
        rows.append(row)
    for host, kind in [(title,'begin'), (title,'instruction'), (title,'separate'), (rows[-1],'end')]:
        run = OxmlElement('w:r')
        node = OxmlElement('w:instrText' if kind == 'instruction' else 'w:fldChar')
        if kind == 'instruction':
            node.text = ' TOC \\f T \\h \\z '
            node.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        else:
            node.set(qn('w:fldCharType'), kind)
        run.append(node)
        host.append(run)
    candidates[0][0].paragraph_format.page_break_before = True
    # Remove empty legacy spacers/breaks in the directory-to-body gap. Necessary
    # section transitions remain; body paragraphs and content are never removed.
    first_body = candidates[0][0]._p
    node = rows[-1].getnext()
    while node is not None and node is not first_body:
        following = node.getnext()
        if (node.tag == qn('w:p') and not Paragraph(node, doc).text.strip()
                and not list(node.iter(qn('w:drawing'))) and not list(node.iter(qn('w:pict')))
                and node.find('.//' + qn('w:sectPr')) is None):
            node.getparent().remove(node)
        node = following
    settings = doc.settings.element
    update = settings.find(qn('w:updateFields'))
    if update is None:
        update = OxmlElement('w:updateFields')
        settings.append(update)
    update.set(qn('w:val'), 'true')
    return True
