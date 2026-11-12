"""Submission directories describe locations; they are never insertion sites."""
import re

from docx.oxml.ns import qn


def cover_nodes(doc, *, initialize=True):
    """Cache one structural front boundary for every pass on this document object."""
    cached = getattr(doc.element, '_cover_nodes', None)
    if cached is not None:
        return cached
    if not initialize:
        return frozenset()
    targets = set()
    for node in doc.element.body.iter(qn('w:instrText'), qn('w:fldSimple')):
        targets.update(re.findall(r'\bPAGEREF\s+([^\s\\]+)', node.text or node.get(qn('w:instr'), '')))
    collected, boundary = [], ''
    for host in doc.element.body:
        if host.tag == qn('w:sectPr'):
            break  # The final section properties do not separate a cover from a body.
        instructions = [n.text or n.get(qn('w:instr'), '')
                        for n in host.iter(qn('w:instrText'), qn('w:fldSimple'))]
        marks = {n.get(qn('w:name'), '') for n in host.iter(qn('w:bookmarkStart'))}
        before = host.find(qn('w:pPr') + '/' + qn('w:pageBreakBefore'))
        before = before is not None and before.get(qn('w:val')) not in {'0', 'false', 'off'}
        if before or any(re.match(r'\s*(?:TOC|PAGEREF)\b', value) for value in instructions) or (
                marks & targets or any(name.startswith('_Toc') for name in marks)):
            boundary = 'before_page_or_directory'
            break
        collected.extend(host.iter())
        if (next(host.iter(qn('w:sectPr')), None) is not None
                or any(n.get(qn('w:type')) == 'page' for n in host.iter(qn('w:br')))):
            boundary = 'after_page_or_section'
            break
    result = frozenset(collected if boundary else ())
    doc.element._cover_nodes = result
    doc.element._cover_boundary = boundary or 'no_structural_front_boundary'
    import logging
    logging.getLogger(__name__).info('COVER_SCOPE boundary=%s paragraphs=%s',
                                   doc.element._cover_boundary, sum(n.tag == qn('w:p') for n in result))
    return result


def _text(node):
    return ''.join(t.text or '' for t in node.iter(qn('w:t'))).strip()


def directory_blocks(doc):
    """Include a tabular directory and its notes until an actual body chapter."""
    from api.services.tenders.word_structure import outline_level

    blocked = set()
    active = False
    for node in doc.element.body:
        if node.tag == qn('w:sectPr'):
            continue
        text = re.sub(r'\s+', '', _text(node))
        title = node.tag == qn('w:p') and bool(re.fullmatch(
            r'(?:(?:第[一二三四五六七八九十\d]+章)|[一二三四五六七八九十\d]+[、.．])?(?:投标|响应)文件(?:格式目录|组成目录)', text))
        table = node.tag == qn('w:tbl') and (
            '提交方式' in text and ('上传端口' in text or '是否有格式要求' in text)
        )
        if title or table:
            active = True
        elif active and node.tag == qn('w:p') and text:
            # Page/section breaks, numbered explanations and attachment mentions
            # cannot turn a directory note into a real submission chapter.
            body_heading = bool(
                text in {'价格文件', '商务文件', '技术文件'} or
                (len(text) < 100 and not re.search(r'[。；;：:]|说明|请针对|目录中', text) and (
                    outline_level(doc, node) or
                    re.match(r'^(?:第[一二三四五六七八九十\d]+[章节]|[一二三四五六七八九十]+[、.]|[（(][一二三四五六七八九十]+[）)])', text)
                ))
            )
            if body_heading:
                active = False
        if active:
            blocked.add(node)
    return blocked


def assert_insertable(doc, anchor):
    body = doc.element.body
    while anchor is not None and anchor.getparent() is not body:
        anchor = anchor.getparent()
    if anchor in directory_blocks(doc):
        raise ValueError('投标文件格式目录及说明区禁止插入附件或占位内容，请定位实际正文章节')
