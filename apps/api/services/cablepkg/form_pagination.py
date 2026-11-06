"""Pagination for newly filled cable bid forms, independent of business data."""
import re

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from .form_reconcile import text, title_kind

GROUPED_KINDS = frozenset({'analysis', 'technical', 'component'})


def _matches_title(value, kind):
    if title_kind(value) == kind:
        return True
    # The quotation template uses the longer official appendix heading.
    return kind == 'quote' and bool(re.search(r'报价汇总表|货物清单行报价', value))


def prepare_chapter(document, original, kind, *, excluded=()):
    """Return the first heading of this form's local introduction, if present.

    Never cross another table, a section boundary, a protected directory, an
    image or unrelated prose. Titles, subtitle and metadata form one keep chain.
    """
    blocked = set(excluded)
    cursor = original.getprevious()
    leading = []
    matched = False
    while cursor is not None and cursor not in blocked and cursor.tag == qn('w:p'):
        value = text(cursor)
        title = _matches_title(value, kind)
        leading_breaks = _leading_page_breaks(cursor)
        page_breaks = [br for br in cursor.iter(qn('w:br')) if br.get(qn('w:type')) == 'page']
        if (next(cursor.iter(qn('w:sectPr')), None) is not None
                or any(next(cursor.iter(qn('w:' + tag)), None) is not None for tag in ('drawing', 'pict', 'object'))
                or (page_breaks and not (title and page_breaks == leading_breaks))):
            break
        metadata = bool(re.match(r'^(?:项目名称|招标编号|分标编号|分标名称|包号|包名称|物资描述|技术规范\s*ID)\s*[:：]', value))
        subtitle = re.fullmatch(r'[（(](?:如有|格式)[）)]', value)
        volume = matched and value in {'商务文件', '技术文件', '价格文件', '报价文件'}
        if value and not (title or metadata or subtitle or volume):
            break
        leading.append(cursor)
        matched = matched or title
        cursor = cursor.getprevious()
    if not matched:
        return None
    leading.reverse()
    # Leave leading blank paragraphs and prior page breaks as they were.
    first = next(node for node in leading if text(node))
    begun = False
    for node in leading:
        begun = begun or node is first
        if not begun:
            continue
        para = Paragraph(node, document._body)
        para.paragraph_format.keep_with_next = True
        para.paragraph_format.keep_together = True
        para.paragraph_format.page_break_before = node is first
        for br in _leading_page_breaks(node):
            br.getparent().remove(br)
    return first


def paginate_group(document, table, *, kind, header_end, label=None, group_index=0, chapter=None):
    """Start each ID group on a page; keep only its header band with data.

    Body rows remain a flowing table. In particular, quotations never become
    one-table-per-goods-row and no page/section-break nodes are inserted.
    """
    if label is not None:
        para = Paragraph(label, document._body)
        para.paragraph_format.keep_with_next = True
        para.paragraph_format.keep_together = True
        para.paragraph_format.page_break_before = (
            kind in GROUPED_KINDS and (group_index > 0 or chapter is None))
    rows = list(table.tr_lst)
    for index, row in enumerate(rows):
        header = index < header_end
        if header:
            props = row.get_or_add_trPr()
            flag = props.find(qn('w:tblHeader'))
            if flag is None:
                flag = OxmlElement('w:tblHeader')
                props.append(flag)
            flag.set(qn('w:val'), '1')
        else:
            # A cloned sample row must not inherit "repeat as header".
            props = row.trPr
            if props is not None:
                for flag in list(props.findall(qn('w:tblHeader'))):
                    props.remove(flag)
        for cell in row.tc_lst:
            for node in cell.findall(qn('w:p')):
                para = Paragraph(node, document._body)
                para.paragraph_format.keep_with_next = header
                para.paragraph_format.page_break_before = False
                if header:
                    para.paragraph_format.keep_together = True


def _leading_page_breaks(node):
    """Return page breaks before visible content; never consume a trailing break."""
    breaks = []
    for child in node.iter():
        if child.tag in {qn('w:t'), qn('w:drawing'), qn('w:pict'), qn('w:object'), qn('w:fldChar'), qn('w:sym')}:
            if child.tag != qn('w:t') or (child.text or '').strip():
                break
        if child.tag == qn('w:br') and child.get(qn('w:type')) == 'page':
            breaks.append(child)
    return breaks


def normalize_duplicate_page_breaks(document):
    """Reconcile only adjacent duplicate boundaries after all content writers.

    Retain sections, drawings, tables, bookmarks and text. A repeated local form
    title belongs with its chapter heading, not on a second title-only page.
    """
    changes = 0
    for para in document.paragraphs:
        if para.paragraph_format.page_break_before is not True:
            continue
        node = para._p
        if next(node.iter(qn('w:sectPr')), None) is not None:
            continue
        cursor = node.getprevious()
        blanks = []
        while cursor is not None and cursor.tag == qn('w:p'):
            if any(next(cursor.iter(qn('w:' + tag)), None) is not None
                   for tag in ('sectPr', 'drawing', 'pict', 'object', 'fldChar', 'sym')):
                break
            if text(cursor):
                kind = title_kind(para.text)
                if kind and kind == title_kind(text(cursor)):
                    previous = Paragraph(cursor, document._body)
                    para.paragraph_format.page_break_before = False
                    previous.paragraph_format.keep_with_next = True
                    previous.paragraph_format.keep_together = True
                    changes += 1
                break
            blanks.append(cursor)
            cursor = cursor.getprevious()
        for blank in blanks:
            breaks = [br for br in blank.iter(qn('w:br')) if br.get(qn('w:type')) == 'page']
            if not breaks:
                continue
            for br in breaks:
                br.getparent().remove(br)
            # Keep bookmark and field anchors. Plain break-only paragraphs have
            # no content and otherwise can themselves overflow a full page.
            if not any(next(blank.iter(qn('w:' + tag)), None) is not None
                       for tag in ('bookmarkStart', 'bookmarkEnd', 'fldSimple', 'hyperlink')):
                blank.getparent().remove(blank)
            changes += len(breaks)
    return changes
