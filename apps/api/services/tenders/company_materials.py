"""Positive company ownership checks before automatic library adoption."""
from pathlib import Path
import logging
import re
from types import SimpleNamespace

from api.services.tenders.company_identity import company_from_document, same_company


_PROFILE_LABELS = {
    "biz": {
        "nature": ("类型", "单位性质"), "address": ("住所", "地址"),
        "established": ("成立日期",), "business_term": ("营业期限", "经营期限"),
        "capital": ("注册资本",),
    },
    "id": {
        "legal_name": ("姓名",), "gender": ("性别",),
        "birthdate": ("出生日期", "出生"), "address": ("住址",),
    },
}


def _profile_tags(raw: str, source: str) -> list[str]:
    fields = _PROFILE_LABELS[source]
    labels = sorted({label for names in fields.values() for label in names}, key=len, reverse=True)
    stop = "|".join(map(re.escape, [*labels, "名称", "法定代表人", "经营范围", "登记机关",
                                  "民族", "公民身份号码", "统一社会信用代码"]))
    tags = []
    for field, names in fields.items():
        pattern = (r"(?<![\u4e00-\u9fff])(?:" + "|".join(map(re.escape, names)) +
                   r")[ \t]*[:：][ \t]*(.*?)(?=[ \t]*(?:" + stop + r")[ \t]*[:：]|[\r\n]|$)")
        values = {match.group(1).strip() for match in re.finditer(pattern, raw) if match.group(1).strip()}
        if len(values) == 1:
            tags.append(f"{field}:{source}:{next(iter(values))}")
        elif values:
            tags.append(f"conflict:{field}:{source}")
    return tags


async def library_company_profile(db, brief) -> tuple[dict, set]:
    """Collect explicit fields from company-scoped licences and legal IDs."""
    from api.services.tenders.library_kb import (
        TENDER_LIB_PUBLIC_ID, _doc_file_path, _list_docs, get_base_by_public_id, key_from_tags,
    )
    from common.errors import AppError

    try:
        base = await get_base_by_public_id(db, TENDER_LIB_PUBLIC_ID)
    except AppError as exc:
        if exc.status_code == 404:
            return {}, set()
        raise
    docs = await _list_docs(db, base.id)
    by_id = {doc.public_id: doc for doc in docs}
    mapping = {
        "nature:biz": "bidderNature", "address:biz": "bidderAddress",
        "established:biz": "companyEstablished", "business_term:biz": "companyBusinessTerm",
        "capital:biz": "companyRegisteredCapital", "legal_name:id": "legalPersonName",
        "gender:id": "legalPersonGender",
    }
    values = {}
    conflicts = set()
    id_addresses = set()
    for doc in docs:
        if _doc_file_path(doc) is None:
            continue
        parent = by_id.get(getattr(doc, "parent_id", None))
        key = (key_from_tags(parent.tags) if parent is not None else "") or key_from_tags(doc.tags)
        if key not in {"license", "id_legal"}:
            continue
        evidence = source_material_evidence(doc, key)
        if not document_matches_company(evidence, brief, key=key):
            continue
        tags = evidence.tags or []
        if key == "id_legal":
            names = {tag.removeprefix("legal_name:id:") for tag in tags if tag.startswith("legal_name:id:")}
            owner = company_from_document(evidence)
            same_person = bool(brief.legalPersonName and names == {brief.legalPersonName})
            if not same_person and not same_company(owner, brief.bidderName):
                continue
            id_addresses.update(tag.removeprefix("address:id:") for tag in tags if tag.startswith("address:id:"))
            if "conflict:address:id" in tags:
                id_addresses.add(None)
        for tag in tags:
            if tag.startswith("conflict:"):
                field = mapping.get(tag.removeprefix("conflict:"))
                if field:
                    conflicts.add(field)
                continue
            parts = tag.split(":", 2)
            field = mapping.get(":".join(parts[:2]))
            if field and len(parts) == 3:
                values.setdefault(field, {}).setdefault(parts[2], f"{'license' if key == 'license' else 'id'}:{doc.public_id}")
    conflicts.update(field for field, candidates in values.items() if len(candidates) > 1)
    if "bidderAddress" not in values and "bidderAddress" not in conflicts and len(id_addresses) == 1 and None not in id_addresses:
        values["bidderAddress"] = {next(iter(id_addresses)): "id:住址"}
    profile = {}
    sources = {}
    for field, candidates in values.items():
        if field not in conflicts:
            profile[field], sources[field] = next(iter(candidates.items()))
    if sources:
        profile["companyFieldSources"] = sources
    return profile, conflicts


def source_material_evidence(doc, key=''):
    """Read explicit subject evidence from retained OCR; never infer from bidder.

    Older ingests may retain plaintext rather than FACTS metadata. Only labelled
    subjects (or one entity on a licence) count; payees/issuers are not owners.
    The result is request-local and does not rewrite OCR or database records.
    """
    if getattr(doc, '_source_evidence_key', None) == key:
        return doc
    from api.services.knowledge.ingest import read_extracted_text
    from api.services.tenders.library_kb import TENDER_LIB_PUBLIC_ID
    from api.services.tenders.company_identity import COMPANY_RE, named_bidders
    raw = read_extracted_text(TENDER_LIB_PUBLIC_ID, getattr(doc, 'public_id', '')) or ''
    if not raw:
        summary = getattr(doc, 'summary', '') or ''
        raw = summary if not summary.startswith(('FACTS:', 'PERFJSON:', 'idcard:')) else ''
    if not raw:
        return doc
    compact = re.sub(r'\s+', '', raw)
    heading = compact[:160]
    detected = ('license' if '营业执照' in heading else
                'bank_permit' if re.search(r'基本存款账户|基本账户(?:信息|开户)|开户许可证',heading) else
                'credit' if re.search(r'公共信用信息报告|信用报告|信用中国',heading) else
                'other_materials' if re.search(r'原厂授权|厂家授权|原厂.*承诺书',heading) else '')
    tags = list(getattr(doc, 'tags', None) or [])
    owners = []
    region = raw
    if detected == 'license':
        # OCR can flatten the register grid to 名称<company>类型<company type>.
        # Read that labelled field only; do not adopt an arbitrary company mention.
        for match in re.finditer(r'(?:^|[\r\n]|登记内容)\s*名称\s*[:：]?\s*(.*?)(?=类型|注册资本|法定代表人|住所|[\r\n]|$)', raw):
            candidate = COMPANY_RE.fullmatch(re.sub(r'\s+', '', match.group(1)).strip())
            if candidate:
                owners.append(candidate.group())
        candidates = list(dict.fromkeys(COMPANY_RE.findall(region)))
        if len(candidates) == 1:
            owners.extend(candidates)
    labels = ('企业名称','公司名称','单位名称','投标人名称','供应商名称','机构名称','主体名称','所属单位','工作单位')
    for label in labels:
        pattern = r'\s*'.join(label) + r'\s*[:：]?\s*([^\r\n]{2,120})'
        for match in re.finditer(pattern, region):
            candidate = COMPANY_RE.search(match.group(1))
            if candidate:owners.append(candidate.group())
    # Preserve row/line boundaries: a following signature line is not part of
    # the company name and must not invalidate a valid bidder label.
    owners.extend(named_bidders(raw))
    if key == 'bond':
        # OCR may flatten a two-column receipt by rows. Resolve the payer from
        # explicit role order, never by finding the current bidder anywhere.
        direct = re.search(r'(?:付款人|汇款人|缴款人)(?:全称|户名|名称)?\s*[:：]?\s*([^\r\n]{2,120})',region)
        candidate = COMPANY_RE.search(direct.group(1)) if direct else None
        if candidate:
            owners.append(candidate.group())
        else:
            payee = region.find('收款人')
            prior = list(dict.fromkeys(COMPANY_RE.findall(region[:payee]))) if payee >= 0 else []
            if len(prior)==1:
                owners.append(prior[0])
            elif len(prior)==2 and '付款人' in region and region.find('付款人') < payee and '全称' in region[:payee]:
                owners.append(prior[0])
    tags.extend('bidder:' + owner for owner in dict.fromkeys(owners))
    for match in re.finditer(r'(?:统一社会信用代码|社会信用代码)[:：]?[^0-9A-Z]{0,100}([0-9A-Z]{18})(?![0-9A-Z])',compact.upper()):
        tags.append('credit_code:' + match.group(1))
    if detected == "license":
        tags.extend(_profile_tags(raw, "biz"))
    elif not detected and key in {"id_legal", "id_agent"}:
        tags.extend(_profile_tags(raw, "id"))
    return SimpleNamespace(public_id=getattr(doc,'public_id',''), summary=getattr(doc,'summary',''),
                           tags=list(dict.fromkeys(tags)), _source_evidence_key=key,
                           _material_type_conflict=bool(detected and key in {'license','bank_permit','credit','bond','iso','finance','product','perf','id_legal','id_agent'}
                             and detected != key))


async def library_bidder_name(db, current=''):
    """Resolve a unique verified full owner name; never choose among companies."""
    if db is None:
        return current
    from api.services.tenders.library_kb import get_base_by_public_id, TENDER_LIB_PUBLIC_ID, _list_docs, _doc_file_path
    from common.errors import AppError
    try:
        base = await get_base_by_public_id(db,TENDER_LIB_PUBLIC_ID)
    except AppError as exc:
        if exc.status_code == 404:
            return current
        raise
    names = []
    for document in await _list_docs(db,base.id):
        name = company_from_document(document)
        if name and name != '__conflicting_company__' and _doc_file_path(document) is not None:
            if not any(same_company(name,known) for known in names):
                names.append(name)
    if current:
        return next((name for name in names if same_company(name,current)),current)
    return names[0] if len(names)==1 else ''


def scope_performance_lines(brief):
    """Saved/manual rows also need ownership; legacy booleans are not evidence."""
    from api.services.tenders.performance import is_perf_placeholder

    kept, skipped = [], 0
    for line in brief.performanceLines:
        if is_perf_placeholder(line) or same_company(line.bidderName, brief.bidderName):
            kept.append(line)
        else:
            skipped += 1
    notes = [f'已排除 {skipped} 项所属公司不符或未核验的业绩，请核对所属投标人后重新选用'] if skipped else []
    return kept, notes


def document_matches_company(doc, brief, *, key=''):
    tags = [str(tag) for tag in (getattr(doc, 'tags', None) or [])]
    if 'material-role:template' in tags:
        declared = [tag.removeprefix('template-bidder:') for tag in tags if tag.startswith('template-bidder:')]
        projects = [tag.removeprefix('template-tender:') for tag in tags if tag.startswith('template-tender:')]
        return bool(declared and projects and brief.tenderNo in projects
                    and all(same_company(owner, brief.bidderName) for owner in declared))
    from api.services.tenders.company_identity import credit_code_from_document,credit_codes_from_document
    doc = source_material_evidence(doc, key)
    if getattr(doc, '_material_type_conflict', False):
        logging.getLogger(__name__).warning('material type conflict file_id=%s key=%s',doc.public_id,key)
        return False
    if len(credit_codes_from_document(doc))>1:
        logging.getLogger(__name__).warning('material company conflict file_id=%s key=%s reason=conflicting_credit_codes',getattr(doc,'public_id',''),key)
        return False
    owner = company_from_document(doc)
    if owner == '__conflicting_company__':
        return False
    code=credit_code_from_document(doc)
    bidder_code=getattr(brief,'bidderCreditCode','')
    logging.getLogger(__name__).info('material company check file_id=%s key=%s owner_present=%s code_present=%s bidder_code_present=%s name_match=%s code_match=%s',getattr(doc,'public_id',''),key,bool(owner),bool(code),bool(bidder_code),same_company(owner,brief.bidderName),bool(code and bidder_code and code==bidder_code))
    # Personal IDs normally contain no employer identity. Their dedicated
    # library group supplies candidate membership; final person selection
    # remains responsible for choosing the exact front/back scans.
    if key in {'id_legal', 'id_agent'} and not owner and not code:
        return True
    matches=same_company(owner,brief.bidderName,left_code=code,right_code=bidder_code)
    if (owner or code) and not matches:
        return False
    if key in {'id_legal', 'id_agent'}:
        from api.services.tenders.id_card import load_id_meta
        card = load_id_meta(getattr(doc, 'summary', '') or '') or {}
        expected = (brief.legalPersonIdNo or brief.legalPersonKey) if key == 'id_legal' else brief.agentIdNo
        if expected:
            normalize=lambda value:str(value or '').strip().upper()
            exact = normalize(expected) in {normalize(card.get('personKey')), normalize(card.get('idNo'))}
            # Candidate scoping is by company; a stale selected person must not
            # remove the very cards needed to rebind. Rendering filters by the
            # final person key after selection.
            return exact or matches
    if owner or code:
        return matches
    return False


async def scoped_library_media(db, brief, media):
    from api.services.tenders.library_kb import (
        get_base_by_public_id, TENDER_LIB_PUBLIC_ID, _list_docs, _doc_file_path, key_from_tags,
    )
    from common.errors import AppError

    try:
        base = await get_base_by_public_id(db, TENDER_LIB_PUBLIC_ID)
    except AppError as exc:
        if exc.status_code != 404:
            raise
        docs = []
    else:
        docs = await _list_docs(db, base.id)
    by_path = {}
    by_id={d.public_id:d for d in docs}
    allowed_ids, person_keys, notes, wrong_types = set(), set(), [], set()
    for doc in docs:
        path = _doc_file_path(doc)
        if path is None:
            continue
        parent=by_id.get(getattr(doc,'parent_id',''))
        own_key=key_from_tags(doc.tags)
        parent_key=key_from_tags(parent.tags) if parent is not None else ''
        key=parent_key or own_key
        by_path[str(path.resolve())] = doc
        evidence = source_material_evidence(doc, key)
        if getattr(evidence,'_material_type_conflict',False):wrong_types.add(doc.public_id)
        matched = document_matches_company(evidence, brief, key=key)
        # A verified material parent can bind an unlabelled child. It cannot
        # override company/code evidence actually present in the child's OCR.
        from api.services.tenders.company_identity import credit_code_from_document,credit_codes_from_document
        child_code=credit_code_from_document(evidence)
        parent_evidence=source_material_evidence(parent,key) if parent is not None else None
        parent_code=credit_code_from_document(parent_evidence) if parent_evidence is not None else ''
        is_template = 'material-role:template' in (doc.tags or [])
        if not is_template and (len(credit_codes_from_document(evidence))>1 or (parent_code and child_code and parent_code!=child_code)):
            matched=False
        elif not matched and parent is not None and not company_from_document(evidence) and not getattr(evidence,'_material_type_conflict',False):
            if not child_code or (parent_code and child_code==parent_code):
                matched=document_matches_company(parent,brief,key=key)
        logging.getLogger(__name__).info('material scope file_id=%s child_key=%s parent_key=%s effective_key=%s matched=%s path=%s',doc.public_id,own_key,parent_key,key,matched,str(path))
        if key in {'id_legal','id_agent'}:
            logging.getLogger(__name__).info('identity company candidate file_id=%s key=%s matched=%s',doc.public_id,key,matched)
        if matched:
            allowed_ids.add(doc.public_id)
            if key == 'id_legal':
                from api.services.tenders.library_kb import library_id_meta
                card = library_id_meta(doc) or {}
                person_keys.add(card.get('personKey') or card.get('idNo'))
    result = {}
    from api.services.tenders.library_kb import library_id_meta
    agent_candidates = {str((library_id_meta(doc) or {}).get('idNo') or '').strip().upper()
                        for doc in docs if doc.public_id in allowed_ids and
                        (key_from_tags(by_id.get(getattr(doc,'parent_id','')).tags)
                         if by_id.get(getattr(doc,'parent_id','')) is not None else key_from_tags(doc.tags)) == 'id_agent'}
    agent_candidates.discard('')
    for key, files in media.items():
        if key == 'tech_drawings':
            result[key] = list(files)  # Already isolated by invitation ID.
            continue
        kept = []
        for value in files:
            path = Path(value)
            doc = by_path.get(str(path.resolve()))
            if doc is not None and doc.public_id in allowed_ids:
                parent=by_id.get(getattr(doc,'parent_id',''))
                source_key=(key_from_tags(parent.tags) if parent is not None else '') or key_from_tags(doc.tags)
                if source_key and source_key!=key:
                    from api.services.tenders.placeholders import DEFAULT_SLOTS
                    from api.services.tenders.match import find_catalog_item
                    target=next((s for s in [*DEFAULT_SLOTS, *brief.extraPlaceholders] if s.key==key),None)
                    source_title=getattr(parent,'name','') if parent is not None else getattr(doc,'name','')
                    explicit_request = source_key == 'other_materials' and 'material-key:' + key in (doc.tags or [])
                    if not explicit_request and (target is None or find_catalog_item([target],title=source_title) is None):
                        notes.append(f'资料「{path.name}」分类与槽位 {key} 不一致，未采用')
                        logging.getLogger(__name__).warning('material mapping rejected file_id=%s source_key=%s requested_key=%s',doc.public_id,source_key,key)
                        continue
                if key == 'id_agent':
                    # Company candidates are broader than renderable evidence;
                    # the authorized agent still requires an exact identity.
                    from api.services.tenders.library_kb import library_id_meta
                    card=library_id_meta(doc) or {}
                    expected=str(brief.agentIdNo or '').strip().upper()
                    if not expected and len(agent_candidates)==1:
                        expected=next(iter(agent_candidates))
                    if not expected or expected not in {str(card.get(field) or '').strip().upper()
                                                         for field in ('idNo','personKey')}:
                        continue
                kept.append(path)
            else:
                if doc is not None and doc.public_id in wrong_types:
                    notes.append(f'资料「{path.name}」正文类型与资料槽位不符，未采用；请上传对应原件')
                else:
                    notes.append(f'资料「{path.name}」所属公司不符或尚未核验，未采用；请在资料库标明所属投标人')
        # Preserve explicit emptiness. A missing mapping entry lets downstream
        # legacy fallback reload files that ownership checks just rejected.
        result[key] = kept
        logging.getLogger(__name__).info('material media key=%s input=%s output=%s paths=%s',key,len(files),len(kept),[str(p) for p in kept])
    return result, allowed_ids, person_keys, list(dict.fromkeys(notes))
