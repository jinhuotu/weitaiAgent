"""Append material supplements without editing any source block."""
import hashlib
import logging
import re

from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from api.services.tenders.word_structure import numbering_info, numbering_labels, outline_level
from api.services.tenders.schema import PlaceholderItem

_ITEM = re.compile(r"^\s*(?:[A-Za-z][.．、）)]|\d+[.．、）)]|[（(][一二三四五六七八九十\d]+[）)])")
_ROOT = re.compile(r"^\s*(?:第[一二三四五六七八九十百\d]+[章节部分]|[一二三四五六七八九十]+[、．.])")
_FORM = re.compile(r"(?:书|函|声明|承诺|协议|合同)\s*$")
_ATTACHMENT_SITE = re.compile(r'此处粘[贴帖]|在此粘[贴帖]|附[：:]|(?:应附|须附|需附|后附|提供)[^。\n]{0,100}(?:复印件|扫描件|凭证|清单)')


def credit_source(text):
    from api.services.tenders.match import material_sources

    sources = material_sources(text)
    return next(iter(sources)) if len(sources) == 1 else ''


def attachment_anchors(body):
    """Find original attachment instructions, including paragraphs in cells."""
    anchors = []
    for block in body:
        for para in block.iter(qn('w:p')):
            if (_ATTACHMENT_SITE.search(_text(para))
                    or (not _text(para).strip() and para.find('w:pPr/w:pBdr',para.nsmap) is not None)):
                anchors.append(para)
    return anchors


def section_type(head, body):
    title = _heading_text(head)
    if re.fullmatch(r'(?:第[一二三四五六七八九十\d]+章)?(?:投标|响应)文件(?:格式|组成)?(?:目录)?', re.sub(r'\s+', '', title)):
        return 'A'
    if re.fullmatch(r'(?:投标|响应|商务|技术|综合)(?:文件|标)(?:[（(].*[）)])?|封面', re.sub(r'\s+','',title)):
        return 'A'
    # Document-composition lists describe the submitted package, not a place
    # where scans should be inserted. Pure letters always take precedence.
    if re.search(r'投标函|响应函|承诺[书函]|合同.*响应书|声明[书函]', title):
        return 'A'
    anchors = attachment_anchors(body)
    enumerated = any(_ITEM.match(_text(el)) for el in body)
    proof_document = bool(re.search(r'身份证明|授权委托|保证金|已标价.*清单', title))
    if proof_document or (anchors and is_form(head, body)):
        return 'B'
    if is_form(head, body):
        return 'A'
    return 'C'


def material_files(slot, media):
    """Use resolved tenderlib01 paths; explicit empty scoped results stay empty."""
    from api.services.tenders.placeholders import _embed_files
    from api.services.tenders.material_identity import unique_material_files
    if media is not None:
        return unique_material_files(_embed_files(slot, media, slot.key))
    from api.services.tenders.slots import list_slot_files, _KEY_RE
    return list_slot_files(slot.key) if _KEY_RE.fullmatch(slot.key or '') else []


def resolve_material_anchor(doc, plan):
    """Refresh only physical boundaries; ownership decisions never change here."""
    from api.services.tenders.placeholders import _body_block
    body = doc.element.body
    def live(node):
        block = _body_block(node) if node is not None else None
        return block if block is not None and block.getparent() is body else None
    original = live(plan.get('anchor'))
    anchor = original
    if anchor is None:
        anchor = live(plan.get('source'))
    head = live(plan.get('head'))
    if head is None:
        head = next((node for node in body if _heading_text(node) == plan.get('chapter')), None)
    if anchor is None:
        anchor = head
    if anchor is None:
        anchor = next((live(node) for node in reversed(plan.get('blocks', [])) if live(node) is not None), None)
    if anchor is None:
        from api.services.tenders.directory_guard import directory_blocks
        if directory_blocks(doc):
            raise ValueError('指定正文章节已失效，禁止在目录或文末建立替代插入点')
        anchor = doc.add_paragraph()._p
    if live(anchor) is None:
        raise ValueError('Unable to relocate material boundary')
    # Synthetic sections have no source attachment blocks. Give every material
    # its own disposable boundary, so replacing one frame cannot invalidate peers.
    if plan.get('fallback'):
        marker = OxmlElement('w:p')
        anchor.addnext(marker)
        anchor = marker
    logging.getLogger(__name__).info(
        'material anchor key=%s chapter=%s anchor_live=%s relocated=%s',
        plan['slot'].key, plan.get('chapter', ''), original is not None,
        original is None or plan.get('fallback', False))
    return anchor


def _fresh_material_plan(decision, scopes):
    """Rehydrate cached ownership against this entrypoint's current body blocks."""
    owners = [(h, b) for h, b, _ in scopes if _heading_text(h) == decision['chapter']]
    index = decision.get('chapter_index', 0)
    head, blocks = owners[index] if index < len(owners) else (None, [])
    def locate(text):
        return next((node for node in [head, *blocks] if node is not None and text and _text(node) == text), None)
    return {**decision, 'head': head, 'blocks': list(blocks),
            'anchor': locate(decision.get('anchor_text')),
            'source': locate(decision.get('source_text'))}


def insert_material(doc, anchor, slot, files, *, wording=''):
    """The section layer selects a boundary; the shared writer owns insertion."""
    from api.services.tenders.placeholders import _drop_after, _body_block
    anchor = _body_block(anchor)
    if anchor is None or anchor.getparent() is not doc.element.body:
        raise ValueError('Material anchor is not a complete body block: '+slot.key)
    original_text = _text(anchor)
    original_xml=anchor.xml
    before=set(doc.element.body)
    try:
        result = _drop_after(doc,anchor,slot,files,complete=True,at_marker=True,wording=wording)
        if original_text and anchor.xml != original_xml:
            raise ValueError('Source block changed during material insertion: '+slot.key)
    except Exception:
        for block in list(doc.element.body):
            if block not in before:
                doc.element.body.remove(block)
        raise
    return result


def is_form(head, body):
    title = _heading_text(head).strip()
    return bool(_FORM.search(title))


def fill_form_gaps(doc, brief):
    """Use existing span writers; never append paragraphs or invent test results."""
    from api.services.tenders.pdf_format import (
        toc_block_ids, _fill_date_placeholders, _fill_mixed_hints,
        _fill_hints, _swap_tokens, _fill_para_blanks, _fill_label_gap,
        _blank_text, _write_blank_span,
    )
    for head, body, rank in sections(doc, set(toc_block_ids(doc))):
        if not is_form(head, body):
            continue
        for block in body:
            for para in block.iter(qn("w:p")):
                for writer in (_fill_date_placeholders, _fill_mixed_hints, _fill_hints,
                               _swap_tokens, _fill_para_blanks, _fill_label_gap):
                    writer(para, brief)
                # Signature/seal fields require execution by the signatory.
                if re.search(r"签字|签章|盖章", _text(para)):
                    continue
                # Unmapped blanks retain the source form; lack of evidence is
                # reported by the existing material diagnostics, never a value.


def _text(el):
    return "".join(t.text or "" for t in el.iter(qn("w:t")))


def _heading_text(el):
    """A floating table inside a paragraph is not part of its heading text."""
    return ''.join(t.text or '' for t in el.iter(qn('w:t'))
                   if next(t.iterancestors(qn('w:tbl')),None) is None).strip()


def _key(value):
    value = re.sub(r"\s+", "", value or "")
    return re.sub(r"^(?:第[一二三四五六七八九十百\d]+[章节部分]|[（(][一二三四五六七八九十\d]+[）)]|[一二三四五六七八九十]+[、．.]|[0-9]+[.．、）)]|[A-Za-z][.．、）)])", "", value).strip('、，,:：')


def _token(prefix, value):
    return prefix + hashlib.sha256(value.encode()).hexdigest()[:24]


def validate_material_uniqueness(doc):
    """Reject duplicated alphabetic source modules outside their first owner."""
    from api.services.tenders.pdf_format import toc_block_ids
    from common.errors import AppError, ErrorCode
    seen, rendered = {}, set()
    excluded = set(toc_block_ids(doc))
    for head, body, rank in sections(doc, excluded):
        for el in body:
            if el.tag != qn('w:p'):
                continue
            label = re.sub(r'\s+', '', _text(el))
            if re.match(r'^[A-Za-z][.．、）)]', label):
                if label in seen and seen[label] is not head:
                    raise AppError(ErrorCode.VALIDATION, f'检测到重复子条目或孤儿模块：{label[:80]}，已停止输出', status_code=422)
                seen[label] = head


def remove_repeated_child_modules(doc):
    """Remove generated short modules duplicating an earlier full source item."""
    from api.services.tenders.pdf_format import toc_block_ids
    excluded = set(toc_block_ids(doc))
    seen, removing, count = [], False, 0
    for el in list(doc.element.body):
        if el in excluded or el.tag == qn('w:sectPr'):
            continue
        value = re.sub(r'\s+', '', _text(el))
        child = el.tag == qn('w:p') and re.match(r'^[A-Za-z][.．、）)]', value)
        boundary = el.tag == qn('w:p') and (_ROOT.match(value) or (outline_level(doc, el) and not child))
        if boundary:
            removing = False
        if child:
            # Source clauses contain full prose; generated duplicate headings
            # use a shortened prefix. Keep the full first source block intact.
            removing = any(previous.startswith(value.rstrip('。:：')) and len(previous) > len(value) for previous in seen)
            if not removing:
                seen.append(value)
            else:
                count += 1
        if removing:
            el.getparent().remove(el)
    return count


def sections(doc, excluded):
    from api.services.tenders.directory_guard import directory_blocks
    excluded = set(excluded) | directory_blocks(doc)
    groups = []
    current = None
    style_cache={}; structure={}
    for el in list(doc.element.body):
        if el in excluded:
            current = None
            continue
        if el.tag == qn("w:sectPr"):
            continue
        text = _heading_text(el)
        rank,info=0,None
        if el.tag==qn('w:p'):
            signature=tuple(str(el.find(path,el.nsmap).xml) if el.find(path,el.nsmap) is not None else ''
                            for path in ('w:pPr/w:pStyle','w:pPr/w:numPr','w:pPr/w:outlineLvl'))
            if signature not in style_cache:
                style_cache[signature]=(outline_level(doc,el),numbering_info(doc,el))
            rank,info=style_cache[signature]
            structure[el]=(rank,info)
        # Alphabetic lists remain children even when Word gives them Heading 1
        # or restarts their own numbering definition at ilvl=0.
        if current is not None and (re.match(r"^\s*[A-Za-z][.．、）)]", text) or (info and info[2] in {'upperLetter', 'lowerLetter'})):
            current[1].append(el)
            continue
        # Numbering definitions outrank stale Word outline styles (real source
        # chapters can all have Heading 6). Parenthesized Chinese lists are
        # physical form subsections, unlike decimal clauses inside a letter.
        numbered_heading = bool(info and info[2].startswith(('chinese', 'ideograph')) and len(text)<100 and not re.search(r'[。；;]',text))
        if numbered_heading:
            rank = info[-1]
        manual_sub = bool(re.match(r'^\s*[（(][一二三四五六七八九十]+[）)]',text) and len(text)<100 and not re.search(r'[。；;]',text))
        heading = el.tag == qn("w:p") and (text=='其他资料（待核对归属）' or numbered_heading or manual_sub or rank or (_ROOT.match(text) and len(text) < 100))
        if heading and text and len(text)<100:
            current = [el, [el] if next(el.iter(qn('w:tbl')),None) is not None else [], rank or 1]
            groups.append(current)
        elif current is not None:
            current[1].append(el)
    doc._element._material_structure=structure
    return groups


def material_name(title):
    """Reject unnamed fragments without an industry-specific admission list."""
    from api.services.cablepkg.word_materials import material_title_valid
    if not material_title_valid(title):
        return ''
    name = re.sub(r'^对应\s*', '', (title or '').strip())
    if not 2 <= len(re.sub(r'\s+', '', name)) <= 120:
        return ''
    if re.fullmatch(r'(?:的)?(?:资料|材料|附件|证明)?(?:要求)?(?:待补充|待核对|名称待核对)?',name.replace(' ','')):
        return ''
    if re.search(r'资料要求待核对|名称待核对|原文完整性待核对|第[零一二三四五六七八九十百\d]+条',name):
        return ''
    return name


def _identity_family(slot, context=''):
    """Classify the document subject, never merge legal and delegated people."""
    from api.services.tenders.match import _kind_hits
    title=re.sub(r'\s+','',slot.title or '')
    roles=_kind_hits(title) & {'id_legal','id_agent'}
    if '身份证' in title:
        if re.search(r'法定代表人|法人代表|法人',title):roles.add('id_legal')
        if re.search(r'委托代理人|授权代理人|被授权人|委托人|代理人',title):roles.add('id_agent')
    if '身份证' not in title and slot.key not in {'id_legal','id_agent'}:
        return ''
    if len(roles)>1:
        # Alternative legal/agent ID wording belongs to the enclosing form,
        # never to a third, generic identity placeholder beside real scans.
        if re.search(r'授权委托|代理人',context):roles={'id_agent'}
        elif re.search(r'法定代表人|法人',context):roles={'id_legal'}
        else:return ''
    family=next(iter(roles),slot.key if slot.key in {'id_legal','id_agent'} else '')
    if not family and '身份证' in title:
        if re.search(r'授权委托|代理人',context):family='id_agent'
        elif re.search(r'法定代表人|法人',context):family='id_legal'
    if family:
        entity=re.match(r'(.+?(?:公司|事务所|合作社|合伙企业))(?=法定代表人|法人代表|法人|委托代理人|授权代理人)',title)
        if entity:family+='_'+hashlib.sha256(entity.group(1).encode()).hexdigest()[:12]
        # Explicitly named different people are different physical materials.
        role=(r'(?:委托代理人|授权代理人|被授权人|代理人)' if family.startswith('id_agent')
              else r'(?:法定代表人|法人代表|法人)')
        subject=re.search(role+r'[:：]?([^，。；;（）()：:\s]{1,12}?)身份证',title)
        if (subject and subject.group(1) not in {'的','本人','有效'}
                and not re.search(r'法定代表人|法人代表|代理人', subject.group(1))):
            family+='_'+hashlib.sha256(subject.group(1).encode()).hexdigest()[:12]
    return family


def _normalized_material_title(title):
    from api.services.tenders.match import normalize_material_title

    title=normalize_material_title(title)
    title=re.sub(r'(?:的)?(?:复印件或扫描件|复印件|扫描件)','',title)
    return re.sub(r'\s+','',re.sub(r'^(?:附[:：]|此处粘[贴帖]|在此粘[贴帖])\s*','',title))


def _recover_conflicting_material_blocks(doc, catalog, media, excluded):
    """Recover only generated blocks whose scoped sources positively conflict."""
    from pathlib import Path
    from api.services.tenders.match import material_content_check, normalize_material_title, _kind_hits, find_catalog_item
    from api.services.tenders.placeholders import _drop_after
    def normalized(value):
        return normalize_material_title(re.sub(r'[“”"《》]', '', value), fold_case=False)
    def bookmark(key):
        return 'result_' + hashlib.sha256(key.encode()).hexdigest()[:20] + '_img'
    headers = [node for node in doc.element.body if node.tag == qn('w:p') and node not in excluded and outline_level(doc, node)]
    rejected = {}
    for slot in catalog:
        paths = [Path(p) for p in (media or {}).get(slot.key, [])]
        checks = [material_content_check(slot, path) for path in paths]
        if not checks or not all(check['reason'] for check in checks):
            continue
        rejected[bookmark(slot.key)] = (slot, checks)
        # Earlier shared recovery wrote source-derived keys. The key protocol,
        # original requests and scoped paths identify those blocks as well.
        for header in headers:
            owner = _heading_text(header)
            for request in catalog:
                title = normalized(request.title)
                if find_catalog_item([slot], title=title, hint=owner) is None:
                    continue
                key = _token('postprocess_', normalized(owner) + title + '\n'.join(map(str, paths)))
                rejected[bookmark(key)] = (PlaceholderItem(key=key, title=title, attachToSection=owner), checks)
    records = []
    owner = ''
    for node in list(doc.element.body):
        if node.getparent() is not doc.element.body or node in excluded:
            continue
        if node.tag == qn('w:p') and outline_level(doc, node):
            owner = _heading_text(node)
        marks = node.xpath('.//w:bookmarkStart/@w:name')
        hit = next((name for name in marks if name in rejected), None)
        if hit is None:
            continue
        slot, checks = rejected[hit]
        actual = {kind for check in checks for kind in check['content_categories']}
        if _kind_hits(owner) & actual:
            continue
        record = {'request': slot.title, 'normalized': normalized(slot.title), 'evidence': owner,
                  'categories': sorted({kind for check in checks for kind in check['expected_categories']}),
                  'content_rejections': checks, 'candidates': [], 'removed_images': 0,
                  'reason': 'content_conflict_block_removed'}
        if _text(node).strip() or any(name != hit for name in marks):
            record['reason'] = 'mixed_content_requires_review'
            records.append(record)
            continue
        anchor = node
        if node.tag == qn('w:tbl'):
            anchor = OxmlElement('w:p')
            node.addprevious(anchor)
        removed_ids = set()
        cursor = node
        while cursor is not None and cursor.tag in {qn('w:p'), qn('w:tbl')}:
            following = cursor.getnext()
            if cursor is not node and ((cursor.tag == qn('w:p') and outline_level(doc, cursor)) or _text(cursor).strip()
                    or cursor.xpath('.//w:bookmarkStart') or next(cursor.iter(qn('w:sectPr')), None) is not None):
                break
            record['removed_images'] += len(cursor.xpath('.//a:blip')) + len(list(cursor.iter('{urn:schemas-microsoft-com:vml}imagedata')))
            removed_ids.update(cursor.xpath('.//@r:embed | .//@r:id | .//@r:link'))
            if cursor is node:
                if node.tag == qn('w:tbl'):
                    cursor.getparent().remove(cursor)
                else:
                    for child in list(cursor):
                        if child.tag != qn('w:pPr'):
                            cursor.remove(child)
            elif next(cursor.iter(qn('w:sectPr')), None) is None:
                cursor.getparent().remove(cursor)
            else:
                break
            cursor = following
        _drop_after(doc, anchor, slot, [], complete=True, at_marker=True)
        referenced = set(doc.element.xpath('.//@r:embed | .//@r:id | .//@r:link'))
        for ident in removed_ids - referenced:
            if ident in doc.part.rels:
                doc.part.drop_rel(ident)
        records.append(record)
        import json
        logging.getLogger(__name__).info('MATERIAL_BLOCK_REJECT %s', json.dumps(record, ensure_ascii=True, sort_keys=True))
    return records


def reconcile_generated_slots(doc, brief, *, slots=None, media=None):
    """Remove superseded local frames, otherwise resolve their scoped materials."""
    import json
    from docx.text.paragraph import Paragraph
    from api.services.tenders.document import _paragraph_role
    from api.services.tenders.directory_guard import directory_blocks
    from api.services.tenders.match import (normalize_material_title, material_sources,
        material_match_rejection, material_content_check, find_catalog_item, _kind_hits, _MATERIAL_SOURCES)
    from api.services.tenders.placeholders import _drop_after

    excluded = directory_blocks(doc)
    catalog = list(slots if slots is not None else brief.extraPlaceholders)
    result = {'changed': 0, 'removed_generated': 0, 'filled_materials': 0,
              'deduplicated_titles': 0, 'diagnostics': []}
    recovered = _recover_conflicting_material_blocks(doc, catalog, media, excluded)
    result['rejected_embedded_blocks'] = sum(row['reason'] == 'content_conflict_block_removed' for row in recovered)
    result['changed'] += result['rejected_embedded_blocks']
    result['diagnostics'].extend(recovered)
    stack = []
    heading_labels = {normalize_material_title(_heading_text(node), fold_case=False)
                      for node in doc.element.body if node.tag == qn('w:p') and outline_level(doc, node)}
    cue = re.compile(r'^\s*(?:此处|在此)粘[贴帖]\s*(?P<title>.*)', re.S)
    def normalized(value):
        return normalize_material_title(re.sub(r'[“”"《》]', '', value), fold_case=False)
    def remove_frame(node):
        if next(node.iter(qn('w:sectPr')), None) is not None:
            for child in list(node):
                if child.tag != qn('w:pPr'):
                    node.remove(child)
            border = node.find('w:pPr/w:pBdr', node.nsmap)
            if border is not None:
                border.getparent().remove(border)
        elif node.getparent() is not None:
            node.getparent().remove(node)
        result['changed'] += 1
    for node in list(doc.element.body):
        if node in excluded or node.tag == qn('w:sectPr'):
            continue
        text = _text(node).strip()
        marker = cue.fullmatch(text) if node.tag == qn('w:p') else None
        level = outline_level(doc, node) if node.tag == qn('w:p') else 0
        if level and not marker:
            while stack and stack[-1]['level'] >= level:
                stack.pop()
            stack.append({'node': node, 'title': text, 'key': normalized(text),
                          'level': level, 'content': False, 'placed': set()})
            continue
        if not marker:
            role = _paragraph_role(Paragraph(node, doc._body))[0] if node.tag == qn('w:p') else 'table'
            evidence = (node.tag == qn('w:tbl') and bool(text)) or (
                role == 'body' and bool(re.search(r'[。；;！？!?]', text)) and not _ATTACHMENT_SITE.search(text))
            if evidence or next(node.iter(qn('w:drawing')), None) is not None:
                for head in stack:
                    head['content'] = True
            continue
        title = normalized(marker['title'])
        labels = {normalized(s.title) for s in catalog} | heading_labels
        for label in sorted(labels - {''}, key=len, reverse=True):
            if title.count(label) > 1:
                first, *remaining = title.split(label)
                title = first + label + ''.join(remaining)
        if title != normalized(marker['title']):
            from api.services.tenders.document import _replace_inline_span
            p = Paragraph(node, doc._body)
            _replace_inline_span(p, 0, len(p.text), text[:marker.start('title')] + title)
            result['deduplicated_titles'] += 1
            result['changed'] += 1
        owner = next((head for head in reversed(stack)
                      if head['key'].removesuffix('表') == title.removesuffix('表')), None)
        if owner and owner['content']:
            remove_frame(node)
            result['removed_generated'] += 1
            continue
        owner = stack[-1] if stack else None
        context = owner['title'] if owner else ''
        demands = [d for d in brief.materialDemands if normalized(d.title) == title]
        hint = ' '.join(d.text for d in demands) or context
        source_groups = material_sources(title)
        requests = [title]
        if len(source_groups) > 1:
            positions = sorted((min(m.start() for m in re.finditer(pattern, title)), source)
                               for source, pattern in _MATERIAL_SOURCES if source in source_groups)
            requests = [title[start:positions[i + 1][0] if i + 1 < len(positions) else len(title)].strip('及和与、 ')
                        for i, (start, _) in enumerate(positions)]
        completed = True
        for request in requests:
            wanted = material_sources(request) or material_sources(hint)
            diagnostics = []
            matched = []
            for candidate in catalog:
                reason = material_match_rejection(request, hint, candidate)
                candidate_sources = material_sources(candidate.title)
                if wanted and (not candidate_sources or not candidate_sources <= wanted):
                    reason = 'source_unclassified_or_mismatched'
                if not reason and find_catalog_item([candidate], title=request, hint=hint) is None:
                    reason = 'title_not_equivalent'
                files = material_files(candidate, media) if not reason and media is not None else []
                content_rejections = [check for path in (media or {}).get(candidate.key, [])
                                      if (check := material_content_check(candidate, path))['reason']]
                if not reason and not files:
                    reason = content_rejections[0]['reason'] if content_rejections else 'no_scoped_files'
                diagnostics.append({'candidate': candidate.title, 'categories': sorted(_kind_hits(candidate.title)),
                                    'reason': reason or 'selected', 'content_rejections': content_rejections})
                if files:
                    matched.append((candidate, files))
            record = {'request': request, 'evidence': hint, 'normalized': normalized(request),
                      'categories': sorted(_kind_hits(request + ' ' + hint)), 'candidates': diagnostics}
            if matched and owner is not None:
                from api.services.tenders.material_identity import unique_material_files
                files = unique_material_files([p for _, paths in matched for p in paths])
                identity = tuple(str(p) for p in files)
                if identity not in owner['placed']:
                    key = _token('postprocess_', owner['key'] + request + '\n'.join(identity))
                    slot = PlaceholderItem(key=key, title=request, hint=hint, attachToSection=context)
                    placed = _drop_after(doc, node, slot, files, complete=True, at_marker=True)
                    if not placed:
                        completed = False
                        record['reason'] = 'writer_failed'
                    else:
                        owner['placed'].add(identity)
                        result['filled_materials'] += 1
                record.setdefault('reason', 'filled_in_scope')
            else:
                completed = False
                record['reason'] = 'no_match' if owner is not None else 'no_section_owner'
            result['diagnostics'].append(record)
            logging.getLogger(__name__).info('MATERIAL_SLOT %s', json.dumps(record, ensure_ascii=True, sort_keys=True))
        if completed:
            remove_frame(node)
        elif len(requests) > 1:
            # Separate unresolved source families; never substitute one source
            # for the other or report the combined request as completed.
            from api.services.tenders.document import _replace_inline_span
            p = Paragraph(node, doc._body)
            unresolved = [x['request'] for x in result['diagnostics'][-len(requests):] if x['reason'] != 'filled_in_scope']
            expected = [text[:marker.start('title')] + name for name in unresolved]
            cursor = node
            for value in expected[1:]:
                from copy import deepcopy
                clone = deepcopy(node)
                for mark in list(clone.iter(qn('w:bookmarkStart'), qn('w:bookmarkEnd'))):
                    mark.getparent().remove(mark)
                paragraph = Paragraph(clone, doc._body)
                _replace_inline_span(paragraph, 0, len(paragraph.text), value)
                cursor.addnext(clone)
                cursor = clone
            if p.text != expected[0]:
                _replace_inline_span(p, 0, len(p.text), expected[0])
                result['changed'] += 1
    return result


def _sentence_boundary(anchor, body):
    """Advance over continued prose, never over another independent list item."""
    if anchor not in body:return anchor
    index=body.index(anchor)
    while index+1<len(body) and anchor.tag==qn('w:p'):
        current=_text(anchor).rstrip()
        following=body[index+1]
        value=_text(following).lstrip()
        if not value and following.tag==qn('w:p'):
            page_before=following.find('w:pPr/w:pageBreakBefore',following.nsmap)
            if (next(following.iter(qn('w:sectPr')),None) is not None
                    or any(br.get(qn('w:type'))=='page' for br in following.iter(qn('w:br')))
                    or (page_before is not None and page_before.get(qn('w:val'),'1') not in {'0','false','off'})):
                break
            index+=1;anchor=following;continue
        if re.search(r'[。；！？;!?][”"）)]?$',current) or following.tag!=qn('w:p'):
            break
        if _ITEM.match(value) or _ROOT.match(value):break
        continuation=bool(re.match(r'^(?:的|和|或|及|与|并|且|而|以及|同时|因此|其中|，|,|[a-z])',value))
        if not continuation:break
        index+=1;anchor=following
    return anchor


def _material_slots(slots, media):
    """Normalize aliases once and retain every resolved original file."""
    from api.services.tenders.material_identity import material_key_aliases, unique_material_files
    from api.services.tenders.match import attachment_requirement_allowed
    slots=[s for s in slots or [] if s.key and material_name(s.title) and attachment_requirement_allowed(s.title,s.hint)]
    exact=material_key_aliases([s.model_copy(update={'title':_normalized_material_title(s.title)}) for s in slots])
    families={}; aliases={}; catalog={}; resolved=dict(media or {})
    for slot in slots:
        family=_standard_material_family(slot) or exact[slot.key]
        families.setdefault(family,[]).append(slot)
    for family,group in families.items():
        representative=next((s for s in group if s.key==family),group[0])
        key=representative.key
        catalog[key]=representative
        files=[]
        for slot in group:
            aliases[slot.key]=key
            if media is not None:files.extend(material_files(slot,media))
        if media is not None:resolved[key]=unique_material_files(files)
        if _standard_material_family(representative):aliases[family]=key
    from api.services.tenders.match import normalize_material_title
    for slot in slots:
        key = aliases.get(slot.key, slot.key)
        files = resolved.get(key, []) if media is not None else material_files(slot, None)
        logging.getLogger(__name__).info(
            'material assembly key=%s normalized=%s fileCount=%s method=%s candidates=%s reasons=%s',
            slot.key, normalize_material_title(slot.title), len(files),
            ('标准 key' if key == slot.key else '标题别名') if files else '未命中',
            [member.title for member in families.get(_standard_material_family(slot) or exact[slot.key], [])],
            [] if files else ['字面不符或来源/主体校验未通过，详见 material match 和 material scope 日志'])
    return catalog, resolved if media is not None else None, aliases


def _standard_material_family(slot):
    from api.services.tenders.match import material_sources

    family=_identity_family(slot,slot.attachToSection)
    if family:return family
    title=_normalized_material_title(slot.title)
    if re.fullmatch(r'(?:本企业|本公司|本单位|企业)?营业执照(?:正本|副本)?',title):return 'license'
    from api.services.tenders.material_identity import canonical_material_key
    family=canonical_material_key(slot)
    if family:
        sources = material_sources(slot.title + ' ' + slot.hint)
        if sources:
            return family + '_' + hashlib.sha256('\n'.join(sorted(sources)).encode()).hexdigest()[:20]
        return family
    return ''


def append_section_materials(doc, slots, media, keys, excluded=(), skip_keys=(), *, auth_enabled=None):
    """Plan ownership once; insert and diagnose exactly that plan."""
    from time import perf_counter
    from api.services.tenders.match import attachment_requests, find_catalog_item,attachment_source_kind,attachment_source_excluded,attachment_requirement_allowed,_kind_hits,material_sources,material_match_rejection
    from api.services.tenders.classifier import material_output_coverage
    from api.services.tenders.placeholders import _body_block, _wants_attach
    from common.errors import AppError, ErrorCode

    started=perf_counter(); logger=logging.getLogger(__name__)
    notes=[]; skipped=set(skip_keys or ())
    # Historical markers are never completion evidence. Only remove metadata,
    # retaining all source runs, tables and text.
    stale=[]; results={}
    for mark in doc.element.body.iter(qn('w:bookmarkStart')):
        if mark.get(qn('w:name'),'').startswith(('supplement_','renderedkey_')):
            stale.append(mark)
        name=mark.get(qn('w:name'),'')
        if name.startswith('result_') and name.endswith(('_img','_box')):
            if name.endswith('_img') and not list(mark.getparent().iter(qn('w:drawing'))):
                stale.append(mark)
                continue
            results.setdefault(name.rsplit('_',1)[0],[]).append(mark)
    for marks in results.values():
        if len(marks)>1:
            winner=next((m for m in marks if m.get(qn('w:name')).endswith('_img') and list(m.getparent().iter(qn('w:drawing')))),marks[0])
            for mark in marks:
                if mark is winner:continue
                host=mark.getparent()
                if host is not winner.getparent() and host.tag==qn('w:p') and host.find('w:pPr/w:pBdr',host.nsmap) is not None:
                    host.getparent().remove(host)
                else:
                    stale.append(mark)
    stale_ids={mark.get(qn('w:id')) for mark in stale}
    for mark in [*stale,*[m for m in doc.element.body.iter(qn('w:bookmarkEnd')) if m.get(qn('w:id')) in stale_ids]]:
        mark.getparent().remove(mark)
    if auth_enabled is not None:doc._element._material_auth_enabled=auth_enabled
    auth_enabled=getattr(doc._element,'_material_auth_enabled',None)
    from api.services.tenders.directory_guard import directory_blocks
    protected_directory = directory_blocks(doc)
    scopes=sections(doc,set(excluded) | protected_directory)
    generated=set()
    for mark in doc.element.body.iter(qn('w:bookmarkStart')):
        if mark.get(qn('w:name'),'').startswith('result_'):
            generated.add(_body_block(mark))
    scopes=[(h,[b for b in body if b not in generated],rank) for h,body,rank in scopes]
    details={h:(section_type(h,b),_key(_heading_text(h)),_key(''.join(_text(el) for el in b)),
                {_key(_text(el).split('：')[0].split(':')[0]) for el in b}) for h,b,_ in scopes}
    source_kinds={}; ancestors=[]; blocked_heads=set()
    for head,body,rank in scopes:
        while ancestors and ancestors[-1][0]>=rank:ancestors.pop()
        ancestors.append((rank,_heading_text(head)))
        if attachment_source_excluded([title for _,title in ancestors]):blocked_heads.add(head)
        fragment=bool(attachment_anchors(body) or any(_ITEM.match(_text(b)) for b in body)
                      or any(re.search(r'格式要求|(?:按|依|参照|采用|使用).{0,20}格式|填报|填写',_text(b)) for b in body))
        source_kinds[head]=attachment_source_kind([title for _,title in ancestors],format_fragment=fragment)
    catalog,media,aliases=_material_slots(slots,media)
    blocked_titles={_key(_heading_text(h)) for h in blocked_heads}
    catalog={k:s for k,s in catalog.items() if _key(s.attachToSection) not in blocked_titles}
    # Migrate earlier alias outcomes to the same canonical result namespace.
    families=getattr(doc._element,'_material_family_keys',{})
    for slot in catalog.values():
        family=_standard_material_family(slot)
        if family and family in families:aliases[families[family]]=slot.key
        if family:families[family]=slot.key
    doc._element._material_family_keys=families
    for mark in doc.element.body.iter(qn('w:bookmarkStart')):
        name=mark.get(qn('w:name'),'')
        for old,new in aliases.items():
            prefix='result_'+hashlib.sha256(old.encode()).hexdigest()[:20]
            if old!=new and name in {prefix+'_img',prefix+'_box'}:
                mark.set(qn('w:name'),'result_'+hashlib.sha256(new.encode()).hexdigest()[:20]+name[-4:])
                break
    # A real image is stronger evidence than any alias's generated empty frame.
    images={m.get(qn('w:name'))[:-4] for m in doc.element.body.iter(qn('w:bookmarkStart'))
            if m.get(qn('w:name'),'').endswith('_img') and list(m.getparent().iter(qn('w:drawing')))}
    for mark in list(doc.element.body.iter(qn('w:bookmarkStart'))):
        name=mark.get(qn('w:name'),'')
        host=mark.getparent()
        if name.endswith('_box') and name[:-4] in images and host.find('w:pPr/w:pBdr',host.nsmap) is not None:
            host.getparent().remove(host)
    # Earlier plans are aliases too, rather than another ownership decision.
    old_plans=getattr(doc._element,'_material_plans',{})
    for old,new in aliases.items():
        if old!=new and old in old_plans:
            previous=old_plans.pop(old)
            previous['slot']=previous['slot'].model_copy(update={'key':new})
            old_plans.setdefault(new,previous)
    auth_heads={h for h,_,_ in scopes if '授权委托' in _key(_heading_text(h))}
    required_agent_heads={head for head,body,_ in scopes if head in auth_heads
                          for site in attachment_anchors(body)
                          if not re.search(r'如有|如非|若|如果',_text(site))
                          and _identity_family(PlaceholderItem(key='',title=_text(site)),_heading_text(head)).startswith('id_agent')}
    if auth_enabled is None:
        auth_enabled=(any(_identity_family(s,s.attachToSection).startswith('id_agent') for s in catalog.values())
                      or any(_identity_family(PlaceholderItem(key='',title=_text(site)),_heading_text(head)).startswith('id_agent')
                             for head,body,_ in scopes if head in auth_heads
                             for site in attachment_anchors(body)))
    # A physical, unconditional attachment site must not remain blank merely
    # because the invitation's inferred authorization flag was left at default.
    auth_enabled = auth_enabled or bool(required_agent_heads)
    def suppressed(slot):
        if _identity_family(slot,slot.attachToSection).startswith('id_agent') and not auth_enabled:
            return True
        return bool(auth_heads and '授权委托书' in slot.title and '身份证' not in slot.title)
    catalog={k:s for k,s in catalog.items() if not suppressed(s)}
    if auth_enabled and auth_heads and not any(_identity_family(s,s.attachToSection).startswith('id_agent') for s in catalog.values()):
        head=next(h for h,_,_ in scopes if h in auth_heads)
        catalog['id_agent']=PlaceholderItem(key='id_agent',title='委托代理人身份证',attachToSection=_heading_text(head))
        aliases['id_agent']='id_agent'
    named=[s.model_copy(update={'hint':''}) for s in catalog.values()]
    structure=doc._element._material_structure
    hints={}; labels=numbering_labels(doc,[el for el,(_,info) in structure.items() if info])
    # Discover original physical submit items before assigning any ownership.
    for head,body,_ in scopes:
        if details[head][0]=='A' or not source_kinds[head]:
            continue
        discovered=False
        for block in body:
            value=_text(block)
            source_label=re.sub(r'^\s*(?:[A-Za-z]|\d+)[.．、）)]\s*','',value).split('：')[0].split(':')[0]
            if _ITEM.match(value) and not attachment_requirement_allowed(source_label,value):continue
            info=structure.get(block,(0,None))[1]
            if info and not _ITEM.match(value):
                value=labels.get(block,('',0))[0]+value
            # Parse a semantic sentence group together while retaining its first
            # physical block as the source anchor.
            end=_sentence_boundary(block,body)
            if end is not block:
                value=''.join(_text(b) for b in body[body.index(block):body.index(end)+1])
            requests=attachment_requests(value,source_kind=source_kinds[head])
            # Explicit paste instructions use the same parser locally; no
            # change to global extraction behavior outside this pipeline.
            if re.search(r'[此这]处粘[贴帖]|在此粘[贴帖]',value):
                cue=re.sub(r'^.*?(?:[此这]处粘[贴帖]|在此粘[贴帖])\s*[:：]?','附：',value)
                requests.extend(attachment_requests(cue+'扫描件' if not re.search(r'扫描件|复印件',cue) else cue,source_kind=source_kinds[head]))
            alpha=re.match(r'^\s*[A-Za-z][.．、）)]\s*(.+)',value,re.S)
            if alpha and len(requests)<=1:
                title=alpha.group(1).split('：')[0].split(':')[0]
                title=re.sub(r'^(?:应|须|需)?提供','',title).strip()
                if material_name(title) and attachment_requirement_allowed(title,value):
                    requests=[PlaceholderItem(key='',title=title,hint=value)]
            for request in requests:
                if not material_name(request.title) or not attachment_requirement_allowed(request.title,value):
                    continue
                family=_identity_family(request,_heading_text(head))
                if suppressed(request.model_copy(update={'attachToSection':_heading_text(head)})):
                    continue
                if family:
                    found=catalog.get(aliases.get(family,family))
                    # Never fall back to a loose role match for named people.
                    request=request.model_copy(update={'key':family})
                else:
                    evidence = request.hint or value
                    sources = material_sources(request.title + ' ' + evidence)
                    eligible = [s for s in named if not material_match_rejection(request.title, evidence, s)
                                and (not sources or material_sources(s.title) <= sources)]
                    found=next((s for s in eligible if _normalized_material_title(s.title)==_normalized_material_title(request.title)),None)
                    found=found or find_catalog_item(eligible,key=request.key,title=request.title,hint=evidence)
                key=found.key if found else (request.key if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}',request.key or '') else 'request_'+hashlib.sha256(request.title.encode()).hexdigest()[:20])
                if key not in catalog:
                    catalog[key]=request.model_copy(update={'key':key,'attachToSection':_heading_text(head)})
                    named.append(catalog[key].model_copy(update={'hint':''}))
                    if family:aliases[family]=key
                hints.setdefault(key,[]).append((head,block,value))
                discovered=True
        # A format instruction may require a whole deliverable without using
        # scan/copy vocabulary. The chapter title supplies its explicit name.
        if not discovered and any(re.search(r'附件|附后|附具|格式要求|(?:按|依|参照|采用|使用).{0,20}格式|填报|填写',_text(b)) for b in body):
            title=_heading_text(head)
            if material_name(title) and attachment_requirement_allowed(title) and not suppressed(PlaceholderItem(key='',title=title,attachToSection=title)) and not any(_key(s.attachToSection)==_key(title) or _key(s.title)==_key(title) for s in catalog.values()):
                key='request_'+hashlib.sha256(_key(title).encode()).hexdigest()[:20]
                catalog[key]=PlaceholderItem(key=key,title=title,attachToSection=title)
                named.append(catalog[key])
    # Cache ownership decisions only; physical insertion boundaries are per entrypoint.
    # A changed title/section is a new plan, never a second opinion mid-render.
    cached=getattr(doc._element,'_material_plans',None)
    if cached is None:
        cached={}; doc._element._material_plans=cached
    plans={}; fallback_count=0
    credit_heads=[h for h,_,_ in scopes if credit_source(_heading_text(h))]
    for key,slot in catalog.items():
        signature=key
        if signature in cached:
            if protected_directory and (cached[signature].get('fallback') or cached[signature].get('head') in protected_directory):
                notes.append('警告：'+slot.title+'：原落位属于目录或未指定章节，已跳过，请核对正文位置')
                continue
            fresh = _fresh_material_plan(cached[signature], scopes)
            if protected_directory and fresh['head'] is None:
                notes.append('警告：'+slot.title+'：指定正文章节已失效，未追加到其他位置')
                continue
            plans[key]=fresh; continue
        name,section=_key(slot.title),_key(slot.attachToSection)
        agent_target = None
        if _identity_family(slot,slot.attachToSection) == 'id_agent':
            candidates = [h for h,_,_ in scopes if h in auth_heads]
            agent_target = next((h for h in candidates if any(row[0] is h for row in hints.get(key,[]))),None)
            if agent_target is None:
                agent_target = next((h for h in candidates if _key(_heading_text(h))==section),next(iter(candidates),None))
        best=None; score=0
        for head,body,_ in scopes:
            kind,heading,blob,item_names=details[head]
            if kind=='A' or head in blocked_heads:
                continue
            exact=hints.get(key,[])
            candidates=[row for row in exact if row[0] is head]
            hit=next((row for row in candidates if _ATTACHMENT_SITE.search(row[2]) and not re.search(r'如有|如非|若|如果',row[2])),None)
            hit=hit or next((row for row in candidates if _ATTACHMENT_SITE.search(row[2])),None) or next(iter(candidates),None)
            rank=200 if name==heading else 180 if hit else 150 if section and section==heading else 100 if name and (name in heading or heading in name) else 0
            if head is agent_target:
                rank = max(rank, 400)
            if credit_source(slot.title) and credit_source(slot.title)==credit_source(_heading_text(head)):
                rank=max(rank,90)
            if (slot.key=='credit' or 'credit' in _kind_hits(slot.title)) and credit_heads:
                wanted=material_sources(slot.title + ' ' + slot.hint)
                actual=material_sources(_heading_text(head))
                if wanted and actual and wanted.isdisjoint(actual):
                    continue
                targets=[h for h in credit_heads if material_sources(_heading_text(h))==wanted]
                if not wanted and len(credit_heads)==1:
                    targets=credit_heads
                if len(targets)==1 and (len(wanted)==1 or not wanted):
                    if head is targets[0]:rank=400
                    elif actual:continue
            if section and section in item_names:
                rank=max(rank,160)
            if section and section!=heading and not hit and rank<160:
                continue
            if not rank and name in blob:
                rank=30
            if rank>score:
                best,score=(head,body,hit),rank
        # Preserve complete letters: their composition lists aren't attachments.
        if best is None and any(kind=='A' and (section==heading or name in heading or name in blob) for kind,heading,blob,_ in details.values()):
            continue
        fallback=False
        if best is None:
            target=_key(section)
            found=next(((h,b,None) for h,b,_ in scopes if h not in blocked_heads and details[h][0]!='A' and target and (target==_key(_heading_text(h)) or target in _key(_heading_text(h)) or _key(_heading_text(h)) in target)),None)
            if found is not None:
                best=found
                logger.debug('material section attachToSection=%s resolved=%s',section,_heading_text(found[0]))
            else:
                if protected_directory:
                    notes.append('警告：'+slot.title+'：未找到指定正文章节，未在目录、说明区或文末追加')
                    continue
                fallback=True
                logger.debug('material section attachToSection=%s fallback',section)
                found=next(((h,b,None) for h,b,_ in scopes if _heading_text(h)=='其他资料（待核对归属）'),None)
                if found is None:
                    head=doc.add_paragraph('其他资料（待核对归属）',style='Normal')._p
                    found=(head,[],None); scopes.append((head,[],1)); details[head]=('C',_key(_heading_text(head)),'',set())
                best=found
                notes.append('警告：'+slot.title+'：所属章节未识别，已降级至「'+_heading_text(best[0])+'」')
        head,body,hit=best
        # Format presence is separate from response completeness. Never ask for
        # a second copy of a table already supplied by this exact section.
        from api.services.cablepkg.form_reconcile import title_kind, form_at_heading
        form_kind = title_kind(slot.title)
        if form_kind and form_at_heading(doc, head, form_kind, excluded=protected_directory):
            continue
        # One immutable block boundary per owned requirement. No note locator,
        # equivalence pass, handled heuristic or further ownership computation.
        endpoint=_body_block(hit[1]) if hit and _ATTACHMENT_SITE.search(hit[2]) else body[-1] if body else head
        tables=[b for b in body if next(b.iter(qn('w:tbl')),None) is not None]
        if tables and endpoint in body and body.index(endpoint)<body.index(tables[-1]):
            endpoint=tables[-1]
        endpoint=_sentence_boundary(endpoint,body)
        # Leave chapter whitespace available to the single writer instead of
        # fixing its insertion boundary after the last empty paragraph.
        from api.services.tenders.placeholders import _slot_blank
        while _slot_blank(doc,endpoint) and endpoint.getprevious() is not None:
            previous=endpoint.getprevious()
            if previous is not head and previous not in body:
                break
            endpoint=previous
        if endpoint.getparent() is not doc.element.body:
            raise AppError(ErrorCode.VALIDATION,'无法定位任何块边界：'+slot.title,status_code=422)
        label=hit[2] if hit and re.match(r'^\s*[A-Za-z][.．、）)]',hit[2]) else slot.title
        plan={'head':head,'chapter':_heading_text(head),'anchor':endpoint,'slot':slot,'label':label,'fallback':fallback,'source':hit[1] if hit else endpoint}
        plan['blocks']=list(body)
        plans[key]=plan
        cached[signature]={name:plan[name] for name in ('chapter','slot','label','fallback')}
        cached[signature].update(anchor_text=_text(endpoint), source_text=_text(plan['source']),
            chapter_index=[h for h,_,_ in scopes if _heading_text(h)==plan['chapter']].index(head))
    planned=perf_counter(); handled=set()
    section_heads={h:_heading_text(h) for h,_,_ in scopes}
    for plan in plans.values():
        plan['section_heads']=section_heads
    # Every owned key enters pending once, including a title-only section.
    positions={node:i for i,node in enumerate(doc.element.body)}
    pending=sorted(plans.items(),key=lambda row:positions.get(row[1]['source'],0))
    existing={m.get(qn('w:name')) for m in doc.element.body.iter(qn('w:bookmarkStart'))}
    for key,plan in reversed(pending):
        token='result_'+hashlib.sha256(key.encode()).hexdigest()[:20]
        if token+'_img' in existing:
            handled.add(key);continue
        slot=plan['slot']; files=material_files(slot,media)
        if token+'_box' in existing:
            if not files:
                handled.add(key);continue
            for mark in list(doc.element.body.iter(qn('w:bookmarkStart'))):
                if mark.get(qn('w:name'))==token+'_box':
                    host=mark.getparent()
                    if host.find('w:pPr/w:pBdr',host.nsmap) is not None:host.getparent().remove(host)
            existing.discard(token+'_box')
        wording='对应 '+plan['label']+'；所需材料：'+material_name(slot.title)
        try:
            anchor=resolve_material_anchor(doc,plan)
            pasted=insert_material(doc,anchor,slot,files,wording=wording)
        except Exception as exc:
            notes.append('警告：'+slot.title+' 渲染异常，尝试文字占位：'+type(exc).__name__)
            logger.warning('material render failed key=%s chapter=%s',key,plan['chapter'],exc_info=True)
            try:
                anchor=resolve_material_anchor(doc,plan)
                pasted=insert_material(doc,anchor,slot,[],wording=wording)
            except Exception as fallback_exc:
                notes.append('警告：'+slot.title+' 占位渲染失败，已跳过：'+type(fallback_exc).__name__)
                logger.warning('material placeholder skipped key=%s chapter=%s',key,plan['chapter'],exc_info=True)
                continue
        handled.add(key)
        existing.add(token+('_img' if pasted else '_box'))
        logger.info('material result key=%s kind=%s files=%s chapter=%s',key,'img' if pasted else 'box',len(files),plan['chapter'])
    rendered=perf_counter()
    report=material_output_coverage(doc,plans)
    for row in report['rows']:
        if row['count']!=1 or row['occurrences']!=1:
            notes.append('警告：资料落位异常 key='+row['key']+' count='+str(row['count'])+' occurrences='+str(row['occurrences']))
    fallback_count=sum(p['fallback'] for p in plans.values())
    ratio=fallback_count/len(plans) if plans else 0
    if ratio>=.05:
        notes.append(f'警告：资料归属兜底 {fallback_count}/{len(plans)}（{ratio:.1%}），请核对章节识别')
    notes.append(f"资料落位核对：{report['expected']} 项，输出 {report['outputs']} 项，未满足 {report['unmet']}")
    logger.info('material phases demands=%s pending=%s img=%s box=%s fallback=%s fallback_ratio=%.4f plan_seconds=%.4f render_seconds=%.4f coverage_seconds=%.4f total_seconds=%.4f',len(plans),len(pending),sum(r['img'] for r in report['rows']),sum(r['box'] for r in report['rows']),fallback_count,ratio,planned-started,rendered-planned,perf_counter()-rendered,perf_counter()-started)
    handled.update(old for old,new in aliases.items() if new in handled)
    return notes,handled
