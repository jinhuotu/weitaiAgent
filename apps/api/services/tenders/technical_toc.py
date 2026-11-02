"""Technical-volume TOC: one structural field, one ordered body projection."""
from copy import deepcopy
import re

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph


def _instruction(node):
    return ' '.join([*(n.text or '' for n in node.iter(qn('w:instrText'))),
                     *(n.get(qn('w:instr'), '') for n in node.iter(qn('w:fldSimple')))])


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
    visited = set()
    while style is not None and style.style_id not in visited:
        visited.add(style.style_id)
        props.append(style.element.find(qn('w:pPr')))
        style = style.base_style
    for p in props:
        n = p.find(qn('w:outlineLvl')) if p is not None else None
        if n is not None:
            return int(n.get(qn('w:val'))) + 1 if int(n.get(qn('w:val'))) < 9 else None
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
    from api.services.tenders.document import _bookmark_paragraph, _write_toc_line
    from api.services.tenders.technical_render import anonymous_text

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
    for p in doc.paragraphs:
        if p._p in excluded or not p.text.strip():
            continue
        marks = {m.get(qn('w:name')) for m in p._p.iter(qn('w:bookmarkStart'))}
        level = _level(doc, p._p)
        # Target bookmarks are body evidence; source point/appendix metadata is
        # used only to recognize existing headings, never to create body text.
        if level is None and not (marks & targets or p.text.strip() in child_titles | appendix_titles):
            continue
        if level is None:
            from api.services.tenders.word_structure import numbering_info
            numbering = numbering_info(doc, p._p)
            if numbering is not None:
                level = numbering[1] + 1
        candidates.append((p, level or (2 if p.text.strip() in child_titles | appendix_titles else 1)))
    if not candidates:
        return False
    from api.services.tenders.word_structure import numbering_labels
    labels = numbering_labels(doc, [p._p for p in doc.paragraphs if p._p not in excluded])
    # Cover headings preceding every directory are not body chapters.
    if regions:
        body = doc.element.body
        first = min(body.index(r[0]) for r in regions)
        candidates = [(p,l) for p,l in candidates if body.index(p._p) > first]
    if not candidates:
        return False
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
        _write_toc_line(Paragraph(row, doc), i, shown, name, '1', label='', level=level)
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
