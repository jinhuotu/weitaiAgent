"""Shared volume TOC: one structural field, one ordered body projection."""
from copy import deepcopy
import json
import logging
import re

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

logger = logging.getLogger('api.tenders.toc')


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


def _title_key(text):
    from api.services.tenders.outline import _strip_outline_prefix
    text = _strip_outline_prefix((text or '').strip())
    # Annotations are immaterial for comparison, including nested annotations.
    previous = None
    while text != previous:
        previous = text
        text = re.sub(r'[（(][^（）()]*[）)]', '', text)
    return text.strip()


def _probe(kind, record):
    logger.info('TOC_%s %s', kind, json.dumps(record, ensure_ascii=True, sort_keys=True))


def _collect_candidates(doc, brief, regions):
    """Scan physical body headings even when package bookmark mapping is complete."""
    from api.services.tenders.word_structure import numbering_info
    from api.services.tenders.document import _paragraph_role
    excluded = {n for region in regions for host in region for n in host.iter()}
    targets = {t for region in regions for t in _targets(region)}
    package = brief.technicalPackage or {}
    known = {}
    for group, level in [('chapters', 1), ('sections', 2), ('tables', 2), ('figures', 2)]:
        for item in package.get(group, []):
            known.setdefault(_title_key(item.get('title', '')), level)
    body = doc.element.body
    first = min((body.index(region[0]) for region in regions), default=-1)
    candidates, records = [], []
    parents, seen = {}, {}
    for p in doc.paragraphs:
        if not p.text.strip():
            continue
        level = _level(doc, p._p)
        marks = {n.get(qn('w:name')) for n in p._p.iter(qn('w:bookmarkStart'))}
        key = _title_key(p.text)
        record = {'paragraph': p._p.get('{http://schemas.microsoft.com/office/word/2010/wordml}paraId')
                  or p._p.getroottree().getpath(p._p), 'level': level, 'outline_level': level, 'title': key}
        reason = ''
        if p._p in excluded or body.index(p._p) <= first:
            reason = 'toc_region'
        elif _paragraph_role(p)[0] in {'information', 'note'}:
            reason = 'other'
        elif level is None:
            if key in known and (not _PAREN_PREFIX.match(p.text) or parents):
                level = known[key]
            elif marks & targets:
                info = numbering_info(doc, p._p)
                level = info[1] + 1 if info else (2 if _PAREN_PREFIX.match(p.text) else 1)
            else:
                reason = 'no_chapter' if key in known and _PAREN_PREFIX.match(p.text) else 'no_outline'
        record['level'] = level
        if not reason and not key:
            reason = 'other'
        if not reason:
            for depth in list(parents):
                if depth >= level:
                    del parents[depth]
            scope = tuple(parents.items())
            identity = (scope, level, key)
            previous = seen.get(identity)
            if previous is not None:
                # Distinct explicit headings can legitimately share a caption;
                # they require separate rows/anchors. Only fallback copies merge.
                if _level(doc, p._p) is None:
                    reason = 'dedup_collision'
                else:
                    _probe('COLLISION', {**record, 'reason': 'distinct_body_heading'})
            if not reason:
                seen[identity] = p._p
                candidates.append((p, level))
                parents[level] = record['paragraph']
        record['reason'] = reason or 'selected'
        records.append(record)
        _probe('REJECT' if reason else 'CANDIDATE', record)
    doc.element._toc_audit = {'candidates': records, 'missing': [], 'numbering_gaps': []}
    return candidates


def _ordinal(text):
    matched = re.match(r'^\s*(?:[（(]([一二三四五六七八九十百千万零〇\d]+)[）)]|(\d+)[.．、](?!\d))', text)
    if not matched:
        return None
    raw = matched[1] or matched[2]
    if raw.isdecimal():
        number = int(raw)
    else:
        digits = dict(zip('零〇一二三四五六七八九', [0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9]))
        total = digit = 0
        for char in raw:
            if char in digits:
                digit = digits[char]
            else:
                total += (digit or 1) * {'十': 10, '百': 100, '千': 1000, '万': 10000}[char]
                digit = 0
        number = total + digit
    return ('parenthesis' if matched[1] else 'decimal', number)


def audit(doc, candidates=None, brief=None):
    """Compare each body target with cached rows; retain source numbering gaps."""
    from api.services.tenders.document import _pageref_bookmark
    regions = _regions(doc)
    excluded = {n for region in regions for host in region for n in host.iter()}
    first = min((doc.element.body.index(r[0]) for r in regions), default=-1)
    if candidates is None:
        candidates = [(p, _level(doc, p._p)) for p in doc.paragraphs
                      if p._p not in excluded and doc.element.body.index(p._p) > first
                      and p.text.strip() and _level(doc, p._p) is not None]
    row_list = [p for p in doc.paragraphs if _pageref_bookmark(p)]
    rows = {_pageref_bookmark(p): p for p in row_list}
    result = getattr(doc.element, '_toc_audit', {})
    missing, gaps, parents, previous = [], [], {}, {}
    source_numbers, body_groups = {}, {}
    for p, level in candidates:
        names = {m.get(qn('w:name')) for m in p._p.iter(qn('w:bookmarkStart'))}
        matches = names & rows.keys()
        shown = p.text
        if brief is not None and brief.techAnonymous:
            from api.services.tenders.technical_render import anonymous_text
            shown = anonymous_text(shown, brief)
        if not any(_title_key(rows[name].text.rsplit('\t', 1)[0]) == _title_key(shown) for name in matches):
            issue = {'title': p.text.strip(), 'level': level, 'reason': 'missing_entry'}
            missing.append(issue)
            _probe('MISSING', issue)
        for depth in list(parents):
            if depth >= level:
                del parents[depth]
        ordinal = _ordinal(p.text)
        scope = tuple(parents.items())
        for name in names:
            body_groups[name] = (scope, level)
        if ordinal:
            group = (scope, level, ordinal[0])
            source_numbers.setdefault(group, set()).add(ordinal[1])
            expected = previous.get(group, 0) + 1
            if ordinal[1] != expected:
                issue = {'title': p.text.strip(), 'level': level, 'expected': expected,
                         'actual': ordinal[1], 'reason': 'source_numbering', 'where': 'body'}
                gaps.append(issue)
                _probe('NUMBERING_GAP', issue)
            previous[group] = ordinal[1]
        parents[level] = p._p.getroottree().getpath(p._p)
    previous = {}
    for row in row_list:
        target = body_groups.get(_pageref_bookmark(row))
        ordinal = _ordinal(row.text.split('\t', 1)[0])
        if target is None or ordinal is None:
            continue
        scope, level = target
        group = (scope, level, ordinal[0])
        expected = previous.get(group, 0) + 1
        if ordinal[1] != expected:
            absent = set(range(expected, ordinal[1])) & source_numbers.get(group, set())
            issue = {'title': row.text.split('\t', 1)[0], 'level': level, 'expected': expected,
                     'actual': ordinal[1], 'reason': 'missing_entry' if absent else 'source_numbering',
                     'where': 'directory'}
            gaps.append(issue)
            _probe('NUMBERING_GAP', issue)
        previous[group] = ordinal[1]
    result.update(body_count=len(candidates), toc_count=len(row_list), missing=missing, numbering_gaps=gaps)
    doc.element._toc_audit = result
    _probe('AUDIT', {k: v for k, v in result.items() if k != 'candidates'})
    return result


def finalize(doc, brief, fmt=None, *, facts=None, slots=None, media=None):
    """One final fill/collect/validate/page path for all generation entry points."""
    from api.services.tenders.document import postprocess_document
    from api.services.tenders.assemble import _fill_toc_pages
    postprocess_document(doc, brief, facts=facts, slots=slots, media=media)
    sync(doc, brief)
    result = getattr(doc.element, '_toc_audit', {})
    notes = [f"TOC_MISSING level={issue['level']} title={issue['title']}"
             for issue in result.get('missing', [])]
    notes.extend(f"TOC_NUMBERING_GAP reason={issue['reason']} level={issue['level']} title={issue['title']}"
                 for issue in result.get('numbering_gaps', []))
    _fill_toc_pages(doc, fmt or brief.documentFormat, subset=True)
    return notes


def sync(doc, brief):
    from api.services.tenders.document import _bookmark_paragraph, _pageref_bookmark, _write_toc_line
    from api.services.tenders.technical_render import anonymous_text

    regions = _regions(doc)
    if not regions:
        # Legacy generated documents can have PAGEREF caches but no enclosing TOC.
        from api.services.tenders.pdf_format import toc_block_ids
        legacy = toc_block_ids(doc)
        if legacy:
            regions = [legacy]
    from api.services.tenders.word_structure import numbering_labels
    candidates = _collect_candidates(doc, brief, regions)
    if not candidates:
        audit(doc, candidates, brief)
        return False
    excluded = {n for region in regions for host in region for n in host.iter()}
    labels = numbering_labels(doc, [p._p for p in doc.paragraphs if p._p not in excluded])
    page_cache = {}
    display_cache = {}
    for para in doc.paragraphs:
        target = _pageref_bookmark(para)
        if not target:
            continue
        title_part, _, page_part = para.text.rpartition('\t')
        if title_part:
            display_cache[target] = title_part.strip()
        if page_part.strip().isdigit():
            page_cache[target] = page_part.strip()
            page_cache.setdefault(_title_key(title_part), page_part.strip())
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
        if all(p._p not in list(body)[start_index + 1:stop_index] for p, _ in candidates):
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
    assigned = set()
    for i, (p, level) in enumerate(candidates):
        if _level(doc, p._p) is None:
            outline = OxmlElement('w:outlineLvl')
            outline.set(qn('w:val'), str(level - 1))
            p._p.get_or_add_pPr().append(outline)
        names = [m.get(qn('w:name')) for m in p._p.iter(qn('w:bookmarkStart'))
                 if m.get(qn('w:name'), '').startswith('toc_')
                 and m.get(qn('w:name')) not in assigned]
        names.sort(key=lambda name: name not in display_cache)
        name = names[0] if names else f'toc_technical_heading_{i}'
        if not names:
            # Avoid clashing with bookmarks elsewhere in the original document.
            all_names = {m.get(qn('w:name')) for m in doc.element.iter(qn('w:bookmarkStart'))}
            while name in all_names:
                name += '_'
            _bookmark_paragraph(p, name)
        assigned.add(name)
        shown = p.text.strip()
        cached = display_cache.get(name, '')
        if cached and _title_key(cached) == _title_key(shown):
            shown = cached
        elif level > 1:
            # A copied root-style ordinal on a child uses the child hierarchy.
            shown = re.sub(r'^([一二三四五六七八九十百千万零〇]+)、', r'（\1）', shown)
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
        page = page_cache.get(name, page_cache.get(_title_key(shown), '1'))
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
    first_body = candidates[0][0]._p
    gap = []
    node = rows[-1].getnext()
    while node is not None and node is not first_body:
        gap.append(node)
        node = node.getnext()
    source_break = next((node for node in reversed(gap)
                         if any(br.get(qn('w:type')) == 'page' for br in node.iter(qn('w:br')))), None)
    if source_break is None:
        candidates[0][0].paragraph_format.page_break_before = True
    # Remove empty legacy spacers/breaks in the directory-to-body gap. Necessary
    # section transitions remain; body paragraphs and content are never removed.
    first_body = candidates[0][0]._p
    node = rows[-1].getnext()
    while node is not None and node is not first_body:
        following = node.getnext()
        if (node is not source_break and node.tag == qn('w:p') and not Paragraph(node, doc).text.strip()
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
    audit(doc, candidates, brief)
    return True
