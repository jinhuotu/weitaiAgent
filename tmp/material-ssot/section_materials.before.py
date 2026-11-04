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
_ATTACHMENT_SITE = re.compile(r'此处粘贴|在此粘贴|附[：:]|(?:应附|须附|需附|后附|提供)[^。\n]{0,100}(?:复印件|扫描件|凭证|清单)')


def credit_source(text):
    if re.search(r'国家企业信用信息公示|公示系统|企业信息公示|股东|投资人信息|企业查询', text):
        return 'enterprise_registry'
    if re.search(r'信用中国|信用报告|失信|重大税收违法', text):
        return 'credit_china'
    return ''


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
    if media is not None:
        return _embed_files(slot, media, slot.key)
    from api.services.tenders.slots import list_slot_files, _KEY_RE
    return list_slot_files(slot.key) if _KEY_RE.fullmatch(slot.key or '') else []


def insert_material(doc, anchor, slot, files):
    """The section layer selects a boundary; the shared writer owns insertion."""
    from api.services.tenders.placeholders import _drop_after, _body_block
    anchor = _body_block(anchor)
    if anchor is None or anchor.getparent() is not doc.element.body:
        raise ValueError('Material anchor is not a complete body block: '+slot.key)
    original_text = _text(anchor)
    result = _drop_after(doc,anchor,slot,files,complete=True,at_marker=True)
    if original_text and _text(anchor) != original_text:
        raise ValueError('Source block changed during material insertion: '+slot.key)
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
                for match in reversed(list(re.finditer(r"[_＿]{2,}", _blank_text(para)))):
                    _write_blank_span(para, match.start(), match.end(), "无")


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
    for mark in doc.element.body.iter(qn('w:bookmarkStart')):
        name = mark.get(qn('w:name'), '')
        if name.startswith('renderedkey_'):
            if name in rendered:
                raise AppError(ErrorCode.VALIDATION, '同一资料 key 被重复渲染，已停止输出', status_code=422)
            rendered.add(name)


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
    groups = []
    current = None
    for el in list(doc.element.body):
        if el in excluded or el.tag == qn("w:sectPr"):
            continue
        text = _heading_text(el)
        rank = outline_level(doc, el) if el.tag == qn("w:p") else 0
        info = numbering_info(doc, el) if el.tag == qn("w:p") else None
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
        heading = el.tag == qn("w:p") and (numbered_heading or manual_sub or rank or (_ROOT.match(text) and len(text) < 100))
        if heading and text and len(text)<100:
            current = [el, [el] if next(el.iter(qn('w:tbl')),None) is not None else [], rank or 1]
            groups.append(current)
        elif current is not None:
            current[1].append(el)
    return groups


def append_section_materials(doc, slots, media, keys, excluded=(), skip_keys=()):
    from api.services.tenders.placeholders import _drop_after, _embed_files, _is_slot_gap, _is_slot_clearance, _wants_attach
    from api.services.tenders.match import attachment_requests, find_catalog_item

    catalog = {s.key: s for s in slots or []}
    allowed = set(keys or ()) | set(catalog)
    skipped = set(skip_keys or ())
    notes, handled = [], set()
    demand_owners = {}
    existing = {b.get(qn("w:name")) for b in doc.element.body.iter(qn("w:bookmarkStart"))}
    for key, slot in catalog.items():
        files = material_files(slot,media)
        logging.getLogger(__name__).info('material match key=%s title=%s mapping_present=%s resolved_files=%s reason=%s',key,slot.title,key in (media or {}),len(files),'readable_file' if files else 'empty_mapping' if key in (media or {}) else 'key_not_in_mapping')
    labels = numbering_labels(doc, list(doc.element.body))
    scopes = sections(doc, set(excluded))
    bond_heads = [head for head,_,_ in scopes if re.search(r'投标保证金|投标担保',_heading_text(head))]
    if bond_heads and 'bond' not in catalog:
        catalog['bond'] = PlaceholderItem(key='bond',title='投标保证金凭证',attachToSection=_heading_text(bond_heads[0]))
        allowed.add('bond')
    owners = {}
    for key, slot in catalog.items():
        best, score = None, 0
        name, section = _key(slot.title), _key(slot.attachToSection)
        for head, body, _ in scopes:
            if section_type(head,body) == 'A':
                continue
            heading = _key(_heading_text(head))
            blob = _key(''.join(_text(el) for el in body))
            rank_score = 0
            if slot.key=='id_legal' and re.search(r'(?:法定代表人|法人).*身份证明',_heading_text(head)):
                rank_score=110
            if slot.key=='id_agent' and '授权委托' in _heading_text(head):
                rank_score=110
            if head in bond_heads and re.search(r'投标保证金|投标担保|汇款凭证|银行保函|基本账户|开户证明',slot.title or ''):
                rank_score = 120
            if name and len(name)>=3 and (name in heading or heading in name):
                rank_score = 100
            if section and section == heading:
                rank_score = max(rank_score,150)
            elif section:
                continue
            if credit_source(slot.title) and credit_source(slot.title)==credit_source(_heading_text(head)):
                rank_score = max(rank_score,90)
            if name and len(name)>=3 and name in blob:
                rank_score = max(rank_score,30)
            if rank_score>score:
                best,score=head,rank_score
        owners[key]=best
    demand_owners.update({key:_heading_text(owners[key]) for key in keys or [] if key in owners and owners[key] is not None})
    unresolved=[]
    for key in keys or []:
        slot=catalog.get(key)
        if slot is None:
            unresolved.append(str(key)+'：未识别资料 key')
        elif owners.get(key) is None and not any(
            section_type(h,b)=='A' and (_key(slot.attachToSection)==_key(_heading_text(h))
                or _key(slot.title) in _key(''.join(_text(el) for el in b))) for h,b,_ in scopes):
            unresolved.append(slot.title+'：所属章节未识别，未追加到文末')
    if unresolved:
        from common.errors import AppError,ErrorCode
        unknown = [key for key in keys or [] if key not in catalog]
        if unknown:
            raise AppError(ErrorCode.VALIDATION,'无法识别资料 key：'+'；'.join(unknown),status_code=422)
        fallback = next((h for h,b,_ in scopes if section_type(h,b)!='A'
                         and re.search(r'其他.*(?:资料|材料)|附件区',_heading_text(h))),None)
        if fallback is None:
            fallback = doc.add_paragraph('其他资料（待核对归属）',style='Heading 1')._p
            scopes.append([fallback,[],1])
        for key in keys or []:
            if owners.get(key) is None and any(message.startswith(catalog[key].title+'：') for message in unresolved):
                owners[key]=fallback
                demand_owners[key]=_heading_text(fallback)
        notes.extend('警告：'+message+'；已降级至「'+_heading_text(fallback)+'」' for message in unresolved)
    for head, body, rank in scopes:
        original = [el for el in body if not any((b.get(qn("w:name")) or "").startswith("result_") for b in el.iter(qn("w:bookmarkStart")))]
        if not original and (head in bond_heads or any(owners.get(key) is head for key in keys or [])):
            original = [head]
        items = []
        for el in original:
            text = _text(el).strip()
            info = numbering_info(doc, el) if el.tag == qn("w:p") else None
            if info and not _ITEM.match(text):
                text = labels.get(el, ('', 0))[0] + text
            if _ITEM.match(text) or info or _wants_attach(text):
                items.append((el, text))
        # A complete signed form takes precedence over incidental numbered clauses.
        title = _heading_text(head).strip()
        category = section_type(head, original)
        if category == 'A':
            continue
        if category == 'C':
            from api.services.tenders.material_layout import constrain_blocks
            constrain_blocks(doc,head,[el for el in original if next(el.iter(qn('w:tbl')),None) is not None])
        if category == 'B':
            from api.services.tenders.placeholders import _body_block
            attachment_blocks = list(dict.fromkeys(_body_block(el) for el in attachment_anchors(original)))
            items = [(el, _text(el)) for el in attachment_blocks if el is not None]
            if not items and original:
                items = [(original[-1],title)]
        else:
            for block in original:
                if block.tag == qn('w:tbl'):
                    items.append((block, _text(block)))
            if not items and original:
                items = [(original[-1], title)]
        pending = []
        for el, text in items:
            requests = attachment_requests(text)
            source_name = re.sub(r'^\s*(?:[A-Za-z]|\d+)[.．、）)]\s*', '', text) or title
            equivalent = find_catalog_item(list(catalog.values()), title=source_name.split('：')[0].split(':')[0], hint=text)
            equivalent_keys = {equivalent.key} if equivalent is not None else set()
            wanted_credit = credit_source(title + text)
            for request in requests:
                # The shared instruction describes every attachment in the
                # paragraph; using it as a match hint merges distinct demands.
                named_catalog = [item.model_copy(update={'hint': ''}) for item in catalog.values()]
                found = find_catalog_item(named_catalog, key=request.key, title=request.title)
                if found is not None:
                    equivalent_keys.add(found.key)
                else:
                    request_key=request.key or 'request_'+hashlib.sha256(request.title.encode()).hexdigest()[:20]
                    if request_key not in catalog:
                        catalog[request_key]=request.model_copy(update={'key':request_key,'attachToSection':title})
                        owners[request_key]=head
                        allowed.add(request_key)
                    if owners.get(request_key) is head:
                        equivalent_keys.add(request_key)
            matched = False
            for key in sorted(allowed, key=lambda key: (not bool((media or {}).get(key)), key not in equivalent_keys, key)):
                slot = catalog.get(key)
                if slot is None or key in handled:
                    continue
                if owners.get(key) is not head:
                    continue
                name = re.sub(r"\s+", "", slot.title or "")
                candidate_credit = credit_source(slot.title or '')
                if wanted_credit and candidate_credit and wanted_credit != candidate_credit:
                    continue
                section = re.sub(r"\s+", "", slot.attachToSection or "")
                # A demand may name the item itself rather than the enclosing
                # chapter. Resolve both through this physical list's membership.
                content = re.sub(r"\s+", "", text or title)
                bond_match = head in bond_heads and (key=='bond' or bool(re.search(r'投标保证金|投标担保|汇款凭证|银行保函|基本账户|开户证明',slot.title or '')))
                identity_match = ((key=='id_legal' and bool(re.search(r'(?:法定代表人|法人).*身份证明',title)))
                                  or (key=='id_agent' and '授权委托' in title))
                if name and (identity_match or bond_match or (wanted_credit and candidate_credit == wanted_credit) or key in equivalent_keys or name in content or any(name in r.title or r.title in name for r in requests)):
                    if key not in skipped:
                        pending.append((slot, text, el))
                    handled.add(key)
                    matched = True
                    if not wanted_credit and not bond_match and len(requests)<2:
                        break
            # Numbered submit lists are source requirements in their own right;
            # extraction/catalog omissions must not suppress an original item.
            alpha = re.match(r"^\s*([A-Za-z])[.．、）)]\s*(.+)", text, re.S)
            if not matched and alpha and category == 'C' and _wants_attach(text):
                source_key = 'item_' + hashlib.sha256((title + '|' + text).encode()).hexdigest()[:24]
                pending.append((PlaceholderItem(key=source_key, title=alpha.group(2), attachToSection=title), text, el))
        for required_key in keys or []:
            if owners.get(required_key) is head and required_key not in handled and original:
                pending.append((catalog[required_key],catalog[required_key].title,original[-1]))
                handled.add(required_key)
        # Distinct requirements in one chapter remain distinct even if only
        # some have files. Per-key insertion handles deduplication; selecting
        # only the real files (or one fallback box) silently drops obligations.
        if not pending:
            continue
        # Reverse insertion at the same original endpoint preserves item order.
        endpoint = original[-1] if original else None
        if endpoint is None:
            continue  # A title alone is never an attachment anchor.
        # The endpoint includes original tables and notes; insert neither in a
        # cell nor between a table's explanation and the original table.
        for slot, text, source_anchor in reversed(pending):
            from api.services.tenders.placeholders import _locate_note
            anchor,_ = _locate_note(doc,slot,set(),excluded,section_head=head,section_body=original)
            from api.services.tenders.placeholders import _body_block
            anchor = _body_block(anchor) if anchor is not None else None
            tables = [block for block in original if block.tag == qn('w:tbl')]
            if tables and anchor in original and original.index(anchor)<original.index(tables[-1]):
                anchor = tables[-1]
            if anchor is None:
                raise ValueError('No block boundary inside the owned section for '+slot.title)
            demand_owners[slot.key] = _heading_text(head)
            result_prefix='result_'+hashlib.sha256(slot.key.encode()).hexdigest()[:20]
            if any(mark.get(qn('w:name')) in {result_prefix+'_img',result_prefix+'_box'}
                   for mark in doc.element.body.iter(qn('w:bookmarkStart'))):
                # Existing outcomes are owned by their actual physical chapter.
                # Reuse them rather than manufacturing another missing-file box.
                for actual_head,actual_body,_ in sections(doc,set(excluded)):
                    if any(mark.get(qn('w:name')) in {result_prefix+'_img',result_prefix+'_box'}
                           for block in actual_body for mark in block.iter(qn('w:bookmarkStart'))):
                        demand_owners[slot.key]=_heading_text(actual_head)
                        notes.append(slot.title+'：复用「'+_heading_text(actual_head)+'」已有资料结果')
                        break
                handled.add(slot.key)
                continue
            short_name = re.split(r'[\n。；;：:]', slot.title or '')[0].strip()
            short_name = re.sub(r'^对应\s*', '', short_name)
            short_name = re.sub(r'^.*(?:应附|须附|需附|提供|规定|要求)[的：:]?', '', short_name).strip()
            if not short_name or len(short_name)>32 or '名称待核对' in short_name:
                demand_owners.pop(slot.key,None)
                notes.append('警告：跳过无明确材料名称的候选 key='+slot.key)
                continue
            labeled = slot.model_copy(update={"title": f"对应{short_name}"})
            files = material_files(slot, media)
            pasted = insert_material(doc, anchor, labeled, files)
            reason = ('已贴真实资料' if pasted else
                      '已匹配文件无法解码或转换，已保留带文字占位' if files else
                      '可用文件列表为空（未上传或未通过归属校验），已保留带文字占位' if slot.key in (media or {}) else
                      '未匹配到可读取文件，已保留带文字占位')
            notes.append(slot.title+'：'+reason+'；所属章节：'+_heading_text(head))
    from api.services.tenders.classifier import material_output_coverage
    report = material_output_coverage(doc,demand_owners)
    # Reparse final physical sections before validation; legacy markers are
    # diagnostic metadata, never evidence that an outcome was rendered.
    physical=[]
    for actual_head,actual_body,_ in sections(doc,set(excluded)):
        for block in actual_body:
            for mark in block.iter(qn('w:bookmarkStart')):
                name=mark.get(qn('w:name'),'')
                if name.startswith(('result_','renderedkey_','supplement_')):
                    physical.append({'bookmark':name,'chapter':_key(_heading_text(actual_head))})
    for row in report['rows']:
        prefix='result_'+hashlib.sha256(row['key'].encode()).hexdigest()[:20]
        logging.getLogger(__name__).info(
            'material result diagnostic key=%s expected=%s img=%s box=%s count=%s bookmarks=%s',
            row['key'],_key(row['chapter']),
            sum(item['bookmark']==prefix+'_img' for item in physical),
            sum(item['bookmark']==prefix+'_box' for item in physical),row['count'],physical)
    report = material_output_coverage(doc,demand_owners)
    notes.append('资料落位核对：'+str(report['expected'])+' 项，输出 '+str(report['outputs'])+' 项，未满足 '+str(report['unmet']))
    if report['unmet'] or report['duplicates']:
        from common.errors import AppError, ErrorCode
        missing = [row['chapter']+' / '+row['key'] for row in report['rows']
                   if row['count'] != 1 or row['occurrences'] != 1]
        raise AppError(ErrorCode.VALIDATION,
                       '资料完整性校验未通过：'+'；'.join(missing),status_code=422)
    return notes, handled
