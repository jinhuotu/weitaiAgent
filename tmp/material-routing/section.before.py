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
    from api.services.tenders.material_identity import unique_material_files
    if media is not None:
        return unique_material_files(_embed_files(slot, media, slot.key))
    from api.services.tenders.slots import list_slot_files, _KEY_RE
    return list_slot_files(slot.key) if _KEY_RE.fullmatch(slot.key or '') else []


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
    except Exception:
        for block in list(doc.element.body):
            if block not in before:
                doc.element.body.remove(block)
        raise
    if original_text and anchor.xml != original_xml:
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
    style_cache={}; structure={}
    for el in list(doc.element.body):
        if el in excluded or el.tag == qn("w:sectPr"):
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
        return ''
    family=next(iter(roles),slot.key if slot.key in {'id_legal','id_agent'} else '')
    if not family and '身份证' in title:
        if re.search(r'授权委托|代理人',context):family='id_agent'
        elif re.search(r'法定代表人|法人',context):family='id_legal'
    if family:
        entity=re.match(r'(.+?(?:公司|事务所|合作社|合伙企业))(?=法定代表人|法人代表|法人|委托代理人|授权代理人)',title)
        if entity:family+='_'+hashlib.sha256(entity.group(1).encode()).hexdigest()[:12]
        # Explicitly named different people are different physical materials.
        subject=re.search(r'(?:法定代表人|法人代表|法人|委托代理人|授权代理人|被授权人|代理人)[:：]?([^，。；;（）()：:\s]{1,12}?)身份证',title)
        if subject and subject.group(1) not in {'的','本人','有效'}:
            family+='_'+hashlib.sha256(subject.group(1).encode()).hexdigest()[:12]
    return family


def _normalized_material_title(title):
    title=re.sub(r'[（(](?:如有|如非[^）)]*|若[^）)]*|如果[^）)]*)[）)]','',title or '')
    title=re.sub(r'(?:的)?(?:复印件或扫描件|复印件|扫描件)','',title)
    return re.sub(r'\s+','',re.sub(r'^(?:附[:：]|此处粘贴|在此粘贴)\s*','',title))


def _sentence_boundary(anchor, body):
    """Advance over continued prose, never over another independent list item."""
    if anchor not in body:return anchor
    index=body.index(anchor)
    while index+1<len(body) and anchor.tag==qn('w:p'):
        current=_text(anchor).rstrip()
        following=body[index+1]
        value=_text(following).lstrip()
        if not value and following.tag==qn('w:p'):
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
    slots=[s for s in slots or [] if s.key and material_name(s.title)]
    exact=material_key_aliases([s.model_copy(update={'title':_normalized_material_title(s.title)}) for s in slots])
    families={}; aliases={}; catalog={}; resolved=dict(media or {})
    for slot in slots:
        family=_identity_family(slot,slot.attachToSection) or exact[slot.key]
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
        if family.startswith(('id_legal','id_agent')):aliases[family]=key
    return catalog, resolved if media is not None else None, aliases


def append_section_materials(doc, slots, media, keys, excluded=(), skip_keys=(), *, auth_enabled=None):
    """Plan ownership once; insert and diagnose exactly that plan."""
    from time import perf_counter
    from api.services.tenders.match import attachment_requests, find_catalog_item
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
    scopes=sections(doc,set(excluded))
    generated=set()
    for mark in doc.element.body.iter(qn('w:bookmarkStart')):
        if mark.get(qn('w:name'),'').startswith('result_'):
            generated.add(_body_block(mark))
    scopes=[(h,[b for b in body if b not in generated],rank) for h,body,rank in scopes]
    details={h:(section_type(h,b),_key(_heading_text(h)),_key(''.join(_text(el) for el in b)),
                {_key(_text(el).split('：')[0].split(':')[0]) for el in b}) for h,b,_ in scopes}
    catalog,media,aliases=_material_slots(slots,media)
    # Migrate earlier alias outcomes to the same canonical result namespace.
    families=getattr(doc._element,'_material_family_keys',{})
    for slot in catalog.values():
        family=_identity_family(slot,slot.attachToSection)
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
    if auth_enabled is None:
        auth_enabled=any(_identity_family(s,s.attachToSection).startswith('id_agent') for s in catalog.values())
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
        if details[head][0]=='A':
            continue
        discovered=False
        for block in body:
            value=_text(block)
            info=structure.get(block,(0,None))[1]
            if info and not _ITEM.match(value):
                value=labels.get(block,('',0))[0]+value
            # Parse a semantic sentence group together while retaining its first
            # physical block as the source anchor.
            end=_sentence_boundary(block,body)
            if end is not block:
                value=''.join(_text(b) for b in body[body.index(block):body.index(end)+1])
            requests=attachment_requests(value)
            # Explicit paste instructions use the same parser locally; no
            # change to global extraction behavior outside this pipeline.
            if re.search(r'[此这]处粘贴|在此粘贴',value):
                cue=re.sub(r'^.*?(?:[此这]处粘贴|在此粘贴)\s*[:：]?','附：',value)
                requests.extend(attachment_requests(cue+'扫描件' if not re.search(r'扫描件|复印件',cue) else cue))
            alpha=re.match(r'^\s*[A-Za-z][.．、）)]\s*(.+)',value,re.S)
            if alpha and len(requests)<=1:
                title=alpha.group(1).split('：')[0].split(':')[0]
                title=re.sub(r'^(?:应|须|需)?提供','',title).strip()
                if material_name(title):
                    requests=[PlaceholderItem(key='',title=title)]
            for request in requests:
                if not material_name(request.title):
                    continue
                family=_identity_family(request,_heading_text(head))
                if suppressed(request.model_copy(update={'attachToSection':_heading_text(head)})):
                    continue
                if family:
                    found=catalog.get(aliases.get(family,family))
                    # Never fall back to a loose role match for named people.
                    request=request.model_copy(update={'key':family})
                else:
                    found=next((s for s in named if _normalized_material_title(s.title)==_normalized_material_title(request.title)),None)
                    found=found or find_catalog_item(named,key=request.key,title=request.title)
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
            if material_name(title) and not suppressed(PlaceholderItem(key='',title=title,attachToSection=title)) and not any(_key(s.attachToSection)==_key(title) or _key(s.title)==_key(title) for s in catalog.values()):
                key='request_'+hashlib.sha256(_key(title).encode()).hexdigest()[:20]
                catalog[key]=PlaceholderItem(key=key,title=title,attachToSection=title)
                named.append(catalog[key])
    # Cache immutable owners/boundaries on this document across shared entrypoints.
    # A changed title/section is a new plan, never a second opinion mid-render.
    cached=getattr(doc._element,'_material_plans',None)
    if cached is None:
        cached={}; doc._element._material_plans=cached
    plans={}; fallback_count=0
    for key,slot in catalog.items():
        signature=key
        if signature in cached:
            plans[key]=cached[signature]; continue
        name,section=_key(slot.title),_key(slot.attachToSection)
        best=None; score=0
        for head,body,_ in scopes:
            kind,heading,blob,item_names=details[head]
            if kind=='A':
                continue
            exact=hints.get(key,[])
            candidates=[row for row in exact if row[0] is head]
            hit=next((row for row in candidates if _ATTACHMENT_SITE.search(row[2]) and not re.search(r'如有|如非|若|如果',row[2])),None)
            hit=hit or next((row for row in candidates if _ATTACHMENT_SITE.search(row[2])),None) or next(iter(candidates),None)
            rank=200 if name==heading else 180 if hit else 150 if section and section==heading else 100 if name and (name in heading or heading in name) else 0
            if credit_source(slot.title) and credit_source(slot.title)==credit_source(_heading_text(head)):
                rank=max(rank,90)
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
            fallback=True
            found=next(((h,b,None) for h,b,_ in scopes if re.search(r'其他.*(?:资料|材料)|附件区',_heading_text(h))),None)
            if found is None:
                head=doc.add_paragraph('其他资料（待核对归属）',style='Normal')._p
                found=(head,[],None)
                scopes.append((head,[],1))
                details[head]=('C',_key(_heading_text(head)),'',set())
            best=found
            notes.append('警告：'+slot.title+'：所属章节未识别，已降级至「'+_heading_text(best[0])+'」')
        head,body,hit=best
        # One immutable block boundary per owned requirement. No note locator,
        # equivalence pass, handled heuristic or further ownership computation.
        endpoint=_body_block(hit[1]) if hit and _ATTACHMENT_SITE.search(hit[2]) else body[-1] if body else head
        tables=[b for b in body if next(b.iter(qn('w:tbl')),None) is not None]
        if tables and endpoint in body and body.index(endpoint)<body.index(tables[-1]):
            endpoint=tables[-1]
        endpoint=_sentence_boundary(endpoint,body)
        if endpoint.getparent() is not doc.element.body:
            raise AppError(ErrorCode.VALIDATION,'无法定位任何块边界：'+slot.title,status_code=422)
        label=hit[2] if hit and re.match(r'^\s*[A-Za-z][.．、）)]',hit[2]) else slot.title
        plan={'head':head,'chapter':_heading_text(head),'anchor':endpoint,'slot':slot,'label':label,'fallback':fallback,'source':hit[1] if hit else endpoint}
        plans[key]=cached[signature]=plan
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
            pasted=insert_material(doc,plan['anchor'],slot,files,wording=wording)
        except (OSError,ValueError,RuntimeError) as exc:
            notes.append('警告：'+slot.title+' 渲染异常，保留文字占位：'+type(exc).__name__)
            pasted=insert_material(doc,plan['anchor'],slot,[],wording=wording)
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
