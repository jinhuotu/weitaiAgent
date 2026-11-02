"""Source-grounded material intent recognition and document outcome accounting."""
from __future__ import annotations

import hashlib
import asyncio
from collections import OrderedDict
from time import perf_counter
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from api.services.tenders.schema import BidBrief, MaterialDemand, PlaceholderItem

logger = logging.getLogger(__name__)
_KEY = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$')
_ACTION = re.compile(r'要求附|应附|须附|需附|附[：:]|提供|提交|出具|递交|持有|具备|随附|随函|备齐|交来|呈交|填报|粘贴|复印|扫描|加盖')
_ARTIFACT = re.compile(r'证书|证明|报告|执照|合同|报表|声明|承诺|凭证|记录|清单|文稿|附件|复印件|扫描件|截图|资料|材料|佐证|纪要')
_NEGATIVE = re.compile(r'(?:无需|无须|不必|免于|免交|免缴|不收取|不要求|不用|不需)(?:再|另行)?(?:提供|提交|附|出具|递交|扫描|复印)?|(?:禁止|不得|不应)(?:提供|提交|附|递交)')
_CHUNK_SIZE = 6000
_MAX_CHUNK_UNITS = 32
_REVIEW_CACHE = OrderedDict()
_REVIEW_CACHE_LIMIT = 256


def compact(text):
    return re.sub(r'\s+', '', str(text or ''))


def material_source_kind(unit):
    """Route by enclosing source headings, never an artifact word in prose."""
    for heading in reversed([*unit.parents, unit.section]):
        name=compact(heading)
        if re.search(r'合同.*条款|法律法规|名词解释|术语定义|流程说明',name):
            return ''
        if re.search(r'资格.*(?:审查|条件)|资格预审',name):
            return 'qualification'
        if re.search(r'评审办法|评分(?:标准|办法|项)|评分细则',name):
            return 'scoring'
        if re.search(r'(?:投标|响应)文件格式|附件清单|资格.*资料',name):
            return 'format'
    return ''


def material_key(title: str) -> str:
    canonical=re.sub(r'[《》「」“”"\s]','',str(title or '')).casefold()
    return 'material_' + hashlib.sha256(canonical.encode()).hexdigest()[:20]


@dataclass
class SourceUnit:
    id: str
    text: str
    section: str = ''
    level: int = 1
    parents: list[str] = field(default_factory=list)


def paragraph_level(doc, el):
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph
    from api.services.tenders.word_structure import outline_level

    props = el.find('w:pPr/w:numPr',el.nsmap)
    style = Paragraph(el,doc).style
    seen = set()
    while props is None and style is not None and style.style_id not in seen:
        seen.add(style.style_id)
        props = style.element.find('w:pPr/w:numPr',style.element.nsmap)
        style = style.base_style
    ilvl = props.find(qn('w:ilvl')) if props is not None else None
    if ilvl is not None:
        return int(ilvl.get(qn('w:val'),'0'))+1
    ind = el.find('w:pPr/w:ind',el.nsmap)
    indent = int(ind.get(qn('w:left'),ind.get(qn('w:start'),'0'))) if ind is not None else 0
    return 1+max(0,indent//420) if indent else (outline_level(doc,el) or 1)


def _units(brief: BidBrief, text: str, source_path: Path | None = None) -> list[SourceUnit]:
    """Read every body paragraph/cell; ilvl is authoritative even for custom styles."""
    raw = []
    if source_path and source_path.suffix.lower() == '.docx' and source_path.is_file():
        from docx import Document
        from docx.oxml.ns import qn
        from docx.text.paragraph import Paragraph
        from api.services.tenders.word_structure import outline_level

        doc = Document(source_path)
        for el in doc.element.body.iter(qn('w:p')):
            p = Paragraph(el, doc)
            value = p.text.strip()
            style = p.style.name if p.style else ''
            if not value or 'PAGEREF' in el.xml or re.match(r'TOC\s*\d|目录\s*\d', style, re.I):
                continue
            # A number's ilvl remains useful even when its numbering definition
            # is missing. Do not infer hierarchy from the material's vocabulary.
            level = paragraph_level(doc,el)
            structural = el.find('w:pPr/w:numPr',el.nsmap) is not None or el.find('w:pPr/w:ind',el.nsmap) is not None
            heading = bool(outline_level(doc, el)) or any(compact(value) == compact(i.title) for i in brief.outlineItems) or (structural and len(value)<80 and not re.search(r'[。；;]',value))
            raw.append((value, level, heading))
    # The extractor can additionally recover text boxes, fields or OCR text.
    # A readable Word body must not suppress those source units.
    known = {compact(i.title): i.level for i in brief.outlineItems if not i.skipped}
    word_blob = compact('\n'.join(row[0] for row in raw))
    context = []
    for value in text.splitlines():
        value = value.strip()
        if not value:
            continue
        numbered = re.match(r'^(?:第.{1,12}[章节部分]|[一二三四五六七八九十]+、|[（(][一二三四五六七八九十0-9]+[）)])', value)
        level = known.get(compact(value), 2 if value.startswith(('（', '(')) else 1)
        heading = compact(value) in known or bool(numbered and len(value) < 60 and not _ACTION.search(value))
        row = (value, level, heading)
        if heading:
            while context and context[-1][1] >= level:
                context.pop()
            context.append(row)
        if word_blob and compact(value) in word_blob:
            continue
        if word_blob and not heading:
            raw.extend(context)
        raw.append(row)
    # In-memory requirements/outline excerpts are also inputs (legacy records and
    # manually edited invitations need not have a saved source file).
    seen = {compact(row[0]) for row in raw}
    source_blob = compact('\n'.join(row[0] for row in raw))
    for item in brief.outlineItems:
        if item.skipped:
            continue
        title=item.rawTitle or item.title
        if title and compact(title) not in seen and compact(item.title) not in seen:
            raw.append((title,item.level,True))
            seen.add(compact(title))
        for value in (item.body, item.inviteExcerpt):
            if value:
                missing = [line for line in value.splitlines() if compact(line)
                           and compact(line) not in seen and compact(line) not in source_blob]
                if missing:
                    raw.append((item.title, item.level, True))
                    for line in missing:
                        raw.append((line, item.level + 1, False))
                        seen.add(compact(line))
    for req in brief.bidRequirements:
        if req.text and compact(req.text) not in seen and compact(req.text) not in source_blob:
            raw.append((req.targetTitle or req.attachToSection or req.sourceChapter, 1, True))
            raw.append((req.text, 2, False))
            seen.add(compact(req.text))
    units, stack = [], []
    for value, level, heading in raw:
        if not value:
            continue
        if heading and not _ACTION.search(value) and ('《' not in value or any(compact(value)==compact(i.title) for i in brief.outlineItems)):
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, value))
        section = stack[-1][1] if stack else ''
        for offset in range(0, len(value), 2200):
            part = value[offset:offset + 2200]
            units.append(SourceUnit('u' + str(len(units)), part, section, level,[title for _,title in stack[:-1]]))
    # Retain section/evidence boundaries while combining adjacent tiny Word
    # paragraphs. The review protocol must not emit thousands of coverage IDs.
    packed = []
    seen_units = set()
    for unit in units:
        signature = (compact(unit.text), unit.section, tuple(unit.parents))
        if signature in seen_units:
            continue
        seen_units.add(signature)
        if (packed and packed[-1].section == unit.section
                and packed[-1].parents == unit.parents
                and len(packed[-1].text) + len(unit.text) < 1800):
            packed[-1].text += '\n' + unit.text
        else:
            packed.append(unit)
    return packed


def source_units(brief: BidBrief, text: str = '', source_path: Path | None = None):
    if source_path is None and re.fullmatch(r'[a-f0-9]{10,16}', brief.invitationId or ''):
        from api.services.tenders.assets import tender_invitations_dir, load_material_source_text
        source_path = tender_invitations_dir() / (brief.invitationId + '.docx')
        text = text or load_material_source_text(brief.invitationId)
    return _units(brief, text, source_path)


def _demand(brief, unit, title, evidence, catalog, *, suggested='', condition='', review=False, mode='attachment', origin='structure'):
    from api.services.tenders.match import attachment_catalog_item
    from api.services.tenders.categories import item_volume, slot_volume

    title = re.sub(r'[《》「」]', '', title).strip()[:180] or '资料要求待核对'
    request = PlaceholderItem(key='', title=title)
    statement = mode=='statement' or bool(re.search(r'声明|承诺',title))
    if re.search(r'资质证书|企业资质|投标人资质',title) and not re.search(r'体系|ISO',title,re.I):
        request.key = 'qualification'
    elif '银行保函' in title:
        request.key = 'bank_guarantee'
    compatible = [s for s in catalog if re.search(r'声明|承诺',s.title)] if statement else catalog
    found = attachment_catalog_item(request, compatible)
    # A semantic mapping is accepted only for an existing key. Unknown keys are
    # converted to deterministic, upload-safe project-local material keys.
    if found is None and suggested and not request.key:
        found = next((slot for slot in compatible if slot.key == suggested), None)
    key = found.key if found is not None else material_key(title)
    owner = next((i for i in brief.outlineItems if compact(i.title) == compact(unit.section)), None)
    volume = item_volume(kind=owner.kind, title=owner.title, key=owner.id) if owner else (
        slot_volume(key, title) if found is not None else 'full')
    section = unit.section
    if not section and found is not None:
        section = found.attachToSection
    ident = hashlib.sha256((key + '\n' + compact(title) + '\n' + compact(section) + '\n' + compact(evidence).rstrip('。；;')).encode()).hexdigest()[:20]
    return MaterialDemand(id='demand_' + ident, sourceId=unit.id, text=evidence.strip(),
                          title=title, slotKey=key, section=section, parentSections=unit.parents, level=max(1, unit.level),
                          volume=volume, condition=condition, reviewRequired=review,
                          origin='review' if review else origin,
                          mode='form' if (mode=='form' or (re.search(r'填报|填写|列出',evidence) and not re.search(r'扫描|复印|证书',evidence))) else
                               'statement' if mode=='statement' or re.search(r'声明|承诺',title) else 'attachment')


def structural_demands(brief, units, catalog):
    """A high-recall structural safety net, not the semantic recognizer's gate."""
    result = []
    for unit in units:
        # A dedicated material/form heading is itself structural evidence, even
        # without a submission verb. Semantics reviews it with the whole source.
        if compact(unit.text)==compact(unit.section) and len(unit.text)<100:
            from api.services.tenders.native_format import _key
            from api.services.tenders.outline import is_invite_pack_title
            title=_key(unit.text)
            concrete=any(m.group() not in {'资料','材料','附件'} for m in _ARTIFACT.finditer(title))
            owner=next((i for i in brief.outlineItems if _key(i.rawTitle or i.title)==title),None)
            eligible=(not re.search(r'[，,。；;]',unit.text) and not is_invite_pack_title(unit.text)
                and not (owner and owner.source=='generate' and owner.kind=='quote'))
            if eligible and (concrete or any(_key(s.title)==title for s in catalog)):
                result.append(_demand(brief,unit,title,unit.text,catalog))
        for sentence in re.split(r'[。；;\n]|[，,](?=但|仍|另|同时|还|并须|并应|并需)', unit.text):
            sentence = sentence.strip()
            if not sentence or _NEGATIVE.search(sentence):
                continue
            action = _ACTION.search(sentence)
            quoted = [m.group(1) for m in re.finditer(r'[《「]([^》」]{2,100})[》」]', sentence)
                      if not re.match(r'(?:第.{0,12}条|规定|要求|所列)',sentence[m.end():])]
            if not action:
                continue
            if not quoted and not _ARTIFACT.search(sentence):
                continue
            if re.match(r'^(?:采购人|招标人|代理机构)(?:应|须|需|将)', sentence):
                continue
            if '文件组成' in sentence or re.search(r'中标后|合同签订后|履约期间', sentence):
                continue
            if quoted:
                titles = ['或'.join(quoted)] if re.search(r'任选|任一|二选一|择一', sentence) else quoted
            else:
                tail = sentence[action.end():].strip(' ：:，,')
                if action.group() in {'复印','扫描'} and sentence[action.end():].startswith('件'):
                    tail = sentence[:action.start()].strip(' ：:，,') or tail.lstrip('件').strip('（）()')
                for mention in re.finditer(r'(?:列出|填报|提交|出具|附上|提供)([^，,。；;]+)',sentence):
                    if _ARTIFACT.search(mention.group(1)):
                        tail = mention.group(1).strip()
                tail = re.sub(r'^(?:有效的?|相应的?|有关的?|以下|下列|一份|本单位的?|加盖公章的?)', '', tail)
                tail = re.sub(r'(?:的)?(?:复印件|扫描件|原件)(?:并.*|且.*|[，,].*)?$', '', tail)
                tail = re.sub(r'[，,](?:并|且|须|应|需).*$', '', tail)
                titles = [tail or sentence]
                if not re.search(r'或者|任选|择一|或',tail):
                    titles = [p for p in re.split(r'[、]|以及|(?<=[证书告明同表件])及',tail) if p.strip()]
            for title in titles:
                condition = 'agent' if re.search(r'(?:如|若|当).{0,16}(?:代理|委托)|(?:代理|委托).{0,16}时',sentence) else ''
                result.append(_demand(brief, unit, title, sentence, catalog,condition=condition))
    return result


def _catalog(catalog):
    from api.services.tenders.placeholders import DEFAULT_SLOTS
    return list({s.key: s for s in [*DEFAULT_SLOTS, *(catalog or [])] if s.key}.values())


def apply_demand_slots(brief: BidBrief, demands):
    merged = {}
    for demand in demands:
        # Repeated source appearances do not create another material/box.
        token = (demand.slotKey, compact(demand.section), compact(demand.title),compact(demand.text).rstrip('。；;'))
        previous = merged.get(token)
        if previous is None or (demand.origin=='semantic' and previous.origin!='semantic'):
            merged[token] = demand
    previous_managed = {s.key for s in brief.extraPlaceholders if s.demandIds}
    brief.materialDemands = list(merged.values())
    inactive = {d.slotKey for d in brief.materialDemands if d.condition=='agent' and not brief.authEnabled}
    inactive -= {d.slotKey for d in brief.materialDemands if d.condition!='agent' or brief.authEnabled}
    inactive |= previous_managed - {d.slotKey for d in brief.materialDemands}
    inactive |= {e.get('slotKey','') for e in brief.materialExemptions} - {d.slotKey for d in brief.materialDemands}
    slots = {s.key: s for s in brief.extraPlaceholders if s.key and s.key not in inactive}
    slots = {key:slot.model_copy(update={'demandIds':[]}) for key,slot in slots.items()}
    brief.requiredSlotKeys = [k for k in brief.requiredSlotKeys if k not in inactive]
    brief.includeSlotKeys = [k for k in brief.includeSlotKeys if k not in inactive]
    for d in brief.materialDemands:
        if d.condition == 'agent' and not brief.authEnabled:
            continue
        if d.slotKey not in slots:
            slots[d.slotKey] = PlaceholderItem(key=d.slotKey, title=d.title,
                hint=d.text, attachToSection=d.section)
        elif not slots[d.slotKey].demandIds or d.origin=='semantic':
            slots[d.slotKey] = slots[d.slotKey].model_copy(update={'title':d.title,'hint':d.text,
                'attachToSection':d.section or slots[d.slotKey].attachToSection})
        if d.id not in slots[d.slotKey].demandIds:
            slots[d.slotKey].demandIds.append(d.id)
        if d.slotKey not in brief.requiredSlotKeys:
            brief.requiredSlotKeys.append(d.slotKey)
        if d.slotKey not in brief.includeSlotKeys:
            brief.includeSlotKeys.append(d.slotKey)
    brief.extraPlaceholders = list(slots.values())


def _with_explicit_demands(brief, demands, catalog):
    represented = {d.slotKey for d in demands}
    explicit = {d.slotKey for d in brief.materialDemands if d.origin=='explicit'}
    extras = {s.key:s for s in brief.extraPlaceholders if s.key and (not s.demandIds or s.key in explicit)}
    known = {s.key:s for s in catalog}
    selected = brief.requiredSlotKeys or brief.includeSlotKeys or list(extras)
    for key in dict.fromkeys(selected):
        if key in represented or not _KEY.fullmatch(key):
            continue
        if any(e.get('slotKey')==key for e in brief.materialExemptions):
            continue
        # A conditional requirement already in the ledger must not be re-created
        # as an unconditional one when the agent checkbox is off.
        if key not in extras and any(d.slotKey==key and d.origin!='explicit' for d in brief.materialDemands):
            continue
        slot = extras.get(key) or known.get(key) or PlaceholderItem(key=key,title=key)
        unit = SourceUnit('explicit:'+key,slot.hint or ('本次列出的资料要求：'+slot.title),slot.attachToSection)
        demand = _demand(brief,unit,slot.title,unit.text,catalog,suggested=key,origin='explicit')
        demand.slotKey = key
        demands.append(demand)
    return demands


async def recognize_material_demands(brief: BidBrief, *, catalog=None, text='', source_path=None, client=None, db=None, persist_source=False, semantic_payload=None):
    from api.services.tenders.prompts import get_prompt
    from api.services.layouts.parse import extract_json_object

    if persist_source and text and re.fullmatch(r'[a-f0-9]{10,16}',brief.invitationId or ''):
        from api.services.tenders.assets import save_material_source_text
        save_material_source_text(brief.invitationId,text)

    catalog = _catalog(catalog)
    all_units = source_units(brief, text, source_path)
    units = [unit for unit in all_units if material_source_kind(unit)]
    distribution = {kind:sum(material_source_kind(unit)==kind for unit in units)
                    for kind in ('qualification','scoring','format')}
    logger.info('material authoritative routing all=%s eligible=%s distribution=%s',
                len(all_units),len(units),distribution)
    fingerprint = hashlib.sha256(json.dumps({'units':[asdict(u) for u in units],
        'catalog':sorted((s.key,s.title) for s in catalog),
        'policy':get_prompt('material_intents_system')},ensure_ascii=False).encode()).hexdigest()
    if brief.materialIntentComplete and fingerprint == brief.materialIntentHash:
        apply_demand_slots(brief, _with_explicit_demands(brief,list(brief.materialDemands),catalog))
        return brief.materialIntentNotes
    demands = []
    review_units = [unit for unit in units if (
        _ACTION.search(unit.text) or _ARTIFACT.search(unit.text)
        or _ARTIFACT.search(unit.section) or re.search(r'《|「|评分|废标|否决', unit.text)
    )]
    logger.info('material routing total_units=%s semantic_units=%s local_units=%s source_chars=%s',
                len(units), len(review_units), len(units)-len(review_units), sum(len(u.text) for u in units))
    chunks, chunk, size = [], [], 0
    for unit in review_units:
        if chunk and (size + len(unit.text) > _CHUNK_SIZE or len(chunk) >= _MAX_CHUNK_UNITS):
            chunks.append(chunk); chunk, size = [], 0
        chunk.append(unit); size += len(unit.text)
    if chunk:
        chunks.append(chunk)
    pending, notes, exemptions = [], [], []
    source_incomplete = False
    if not text and re.fullmatch(r'[a-f0-9]{10,16}',brief.invitationId or ''):
        from api.services.tenders.assets import tender_invitations_dir, load_material_source_text, _INVITE_TEXT_MAX

        folder=tender_invitations_dir()
        complete_word = bool(source_path and Path(source_path).suffix.lower()=='.docx' and Path(source_path).is_file()) or (folder/(brief.invitationId+'.docx')).is_file()
        source_incomplete = (not complete_word and not (folder/(brief.invitationId+'.materials.txt')).is_file()
            and len(load_material_source_text(brief.invitationId))>=_INVITE_TEXT_MAX)
    if units and client is None:
        try:
            from api.services.models.runtime import build_llm_client
            client = await build_llm_client(db, 'fast')
        except Exception as exc:
            logger.warning('material semantic client unavailable: %s', type(exc).__name__)
    gate = asyncio.Semaphore(6)
    async def review(index, chunk):
        messages = [
            {'role':'system','content':get_prompt('material_intents_system')},
            {'role':'user','content':json.dumps({'catalog':[],
                'units':[asdict(u) for u in chunk]},ensure_ascii=False)},
        ]
        identity = [getattr(client, field, None) for field in ('api_base', 'model', 'temperature')]
        identity.append(hashlib.sha256(str(getattr(client, 'api_key', '')).encode()).hexdigest())
        key = hashlib.sha256(json.dumps([identity, messages], ensure_ascii=False).encode()).hexdigest()
        if key in _REVIEW_CACHE:
            _REVIEW_CACHE.move_to_end(key)
            logger.info('material review chunk=%s units=%s cache=hit', index, len(chunk))
            return _REVIEW_CACHE[key]
        async with gate:
            started = perf_counter()
            try:
                if client is None:
                    raise RuntimeError('semantic model unavailable')
                import inspect
                options = {'mode': 'fast'}
                if 'response_format' in inspect.signature(client.complete).parameters:
                    options['response_format'] = {'type': 'json_object'}
                raw = await client.complete(messages, **options)
                payload = extract_json_object(raw, prefer_keys=('reviewedUnitIds', 'requirements'))
                if not isinstance(payload, dict) or not isinstance(payload.get('reviewedUnitIds'), list) or not isinstance(payload.get('requirements'), list):
                    raise ValueError('missing review coverage')
                if {str(i) for i in payload['reviewedUnitIds']} >= {u.id for u in chunk}:
                    _REVIEW_CACHE[key] = payload
                    while len(_REVIEW_CACHE) > _REVIEW_CACHE_LIMIT:
                        _REVIEW_CACHE.popitem(last=False)
                return payload
            except Exception as exc:
                return exc
            finally:
                logger.info('material review chunk=%s/%s units=%s seconds=%.3f', index, len(chunks), len(chunk), perf_counter() - started)
    started = perf_counter()
    if semantic_payload is not None:
        # Reuse the single full-coverage map pass; no second model read of source.
        intents = semantic_payload.get('materialIntents')
        complete = semantic_payload.get('materialReviewComplete') is True and isinstance(intents, list)
        routed = {}
        invalid = False
        normalized = [(u, compact(u.text)) for u in review_units]
        for row in intents if isinstance(intents, list) else []:
            if not isinstance(row, dict) or not row.get('evidence') or not row.get('title'):
                invalid = True
                continue
            evidence = compact(row['evidence'])
            owner = next((u for u, content in normalized if evidence in content), None)
            if owner is None:
                invalid = True
                continue
            routed.setdefault(owner.id, []).append({**row, 'sourceId': owner.id})
        results = [{'reviewedUnitIds':[u.id for u in chunk] if complete and not invalid else [],
                    'requirements':[r for u in chunk for r in routed.get(u.id, [])]}
                   for chunk in chunks]
        logger.info('material review reused_map=true complete=%s invalid_evidence=%s units=%s additional_llm_calls=0',complete,invalid,len(review_units))
    else:
        results = await asyncio.gather(*(review(index, chunk) for index, chunk in enumerate(chunks, 1)))
    logger.info('material review complete chunks=%s units=%s seconds=%.3f', len(chunks), len(units), perf_counter() - started)
    for chunk, result in zip(chunks, results):
        by_id = {u.id: u for u in chunk}
        reviewed = set()
        try:
            if isinstance(result, Exception):
                raise result
            payload = result
            if not isinstance(payload, dict) or not isinstance(payload.get('reviewedUnitIds'), list) or not isinstance(payload.get('requirements'), list):
                raise ValueError('missing review coverage')
            reviewed = set(str(i) for i in payload['reviewedUnitIds']) & set(by_id)
            for row in payload['requirements']:
                if not isinstance(row, dict):
                    reviewed = set()
                    continue
                unit = by_id.get(str(row.get('sourceId') or ''))
                evidence, title = str(row.get('evidence') or '').strip(), str(row.get('title') or '').strip()
                from api.services.tenders.company_identity import COMPANY_RE

                fabricated = any(value not in evidence for value in re.findall(r'\d+(?:\.\d+)?',title))
                fabricated = fabricated or any(name not in evidence and name!=brief.bidderName for name in COMPANY_RE.findall(title))
                if unit is None or not evidence or compact(evidence) not in compact(unit.text) or not title or fabricated:
                    if unit:
                        reviewed.discard(unit.id)
                    else:
                        reviewed = set()
                    continue
                demands.append(_demand(brief,unit,title,evidence,catalog,suggested=str(row.get('key') or ''),
                    condition='agent' if row.get('condition') == 'agent' else '',
                    mode=row.get('mode') if row.get('mode') in {'form','statement'} else 'attachment',origin='semantic'))
            for row in payload.get('exemptions') or []:
                if not isinstance(row,dict):
                    reviewed=set();continue
                unit=by_id.get(str(row.get('sourceId') or ''))
                evidence,title=str(row.get('evidence') or '').strip(),str(row.get('title') or '').strip()
                negative=bool(_NEGATIVE.search(evidence) or re.search(r'是否[^。；;]{0,40}[:：]\s*(?:否|无|不适用)',evidence))
                if unit is None or not title or not negative or compact(evidence) not in compact(unit.text):
                    reviewed=set();continue
                mapped=_demand(brief,unit,title,evidence,catalog,suggested=str(row.get('key') or ''))
                exemptions.append({'slotKey':mapped.slotKey,'title':title,'text':evidence,'sourceId':unit.id})
        except ValueError as exc:
            # Specific handling for ValueError from semantic chunk processing
            reviewed = set()
            logger.warning(
                'material semantic chunk incomplete: ValueError: %s chunk_size=%d',
                str(exc)[:200],
                len(chunk),
            )
        except Exception as exc:
            reviewed = set()
            logger.warning(
                'material semantic chunk incomplete: %s: %s',
                type(exc).__name__,
                str(exc)[:200],
            )
        # Completed semantic review supersedes lexical candidates in that unit;
        # retain the structural safety net only where review did not complete.
        demands=[d for d in demands if d.origin!='structure' or d.sourceId not in reviewed]
        pending.extend(u for u in chunk if u.id not in reviewed)
    demands.extend(structural_demands(brief, pending, catalog))
    # Do not silently call an unreviewed chapter "no requirements". A review box
    # is distinct from a confirmed demand and is explicitly reported as such.
    held = set()
    for unit in (pending if semantic_payload is None else []):
        if unit.section in held:
            continue
        held.add(unit.section)
        demands.append(_demand(brief,unit,'资料要求待核对',
            '本区段资料要求尚未完成语义核验，请对照原招标文件检查，勿视为无资料需求。',
            catalog,review=True))
    if pending:
        notes.append(f'资料语义核验未完成：{len(pending)} 个原文单元待人工核对；已保留需求并补核对占位，未宣称全部识别完成')
    if source_incomplete:
        unit=SourceUnit('source-tail','旧原文缓存可能截断，无法确认尾部资料要求，请重新上传完整原文。')
        demands.append(_demand(brief,unit,'原文完整性待核对',unit.text,catalog,review=True))
        notes.append(unit.text)
    brief.materialExemptions=exemptions
    demands=_with_explicit_demands(brief,demands,catalog)
    unique = OrderedDict()
    rejected = 0
    for demand in demands:
        name=compact(demand.title)
        from api.services.tenders.section_materials import material_name
        if not material_name(demand.title):
            rejected+=1
            continue
        if demand.origin!='explicit' and (not 2<=len(name)<=60 or re.search(r'[。；;]|《.+》|第[零一二三四五六七八九十百\d]+条',name)):
            rejected+=1
            continue
        signature=demand.slotKey or material_key(name)
        unique.setdefault(signature,demand)
    demands=list(unique.values())
    logger.info('material recognition demands=%s rejected_fragments=%s source_distribution=%s',len(demands),rejected,distribution)
    if len(demands)>=200:
        notes.append(f'警告：资料抽取数量较多：{len(demands)}项；来源分布：{distribution}，请核查识别结果')
    demands=[d for d in demands if not any(d.slotKey==e['slotKey'] and
        (d.origin=='explicit' or compact(d.title)==compact(e['title'])) for e in exemptions)]
    notes.extend('资料「'+e['title']+'」按原文免交/不适用，本次未纳入；依据：'+e['text'][:160] for e in exemptions)
    apply_demand_slots(brief,demands)
    brief.materialIntentHash, brief.materialIntentComplete = fingerprint, not pending and not source_incomplete
    brief.materialIntentNotes = notes
    return notes


def prepare_structural_demands(brief, catalog=None):
    """Synchronous builders retain structural requirements; async API adds semantics."""
    if brief.materialDemands or brief.materialIntentComplete:
        apply_demand_slots(brief, _with_explicit_demands(brief,list(brief.materialDemands),_catalog(catalog)))
        return
    units = source_units(brief)
    demands = structural_demands(brief, units, _catalog(catalog))
    apply_demand_slots(brief, _with_explicit_demands(brief,demands,_catalog(catalog)))


def applicable(demand, brief, volume):
    return (not (demand.condition == 'agent' and not brief.authEnabled)
            and (volume not in {'business','technical'} or demand.volume in {volume,'full',''}))


def coverage(brief, volume):
    demands=brief.materialDemands
    if volume=='technical' and brief.technicalPackage:
        from api.services.tenders.technical_package import scoped_inputs
        catalog=[PlaceholderItem(key=d.slotKey,title=d.title,hint=d.text,attachToSection=d.section) for d in demands]
        demands=scoped_inputs(brief,catalog,{})[0].materialDemands
    return [{'id':d.id,'title':d.title,'text':d.text,'section':d.section,'level':d.level,
             'slotKey':d.slotKey,'status':d.outcomes.get(volume,''),'reviewRequired':d.reviewRequired,
             'placement':d.placements.get(volume,'')}
            for d in demands if applicable(d,brief,volume)]


def sync_material_outcomes(target, rendered):
    """Deep-copy render branches must return their verified outcomes to the record."""
    by_id={d.id:d for d in target.materialDemands}
    for demand in rendered.materialDemands:
        if demand.id in by_id:
            by_id[demand.id].outcomes.update(demand.outcomes)
            by_id[demand.id].placements.update(demand.placements)
        else:
            target.materialDemands.append(demand.model_copy(deep=True))


def enforce_material_outcomes(doc, brief, *, media=None, slots=None, volume='full'):
    """Supplement list sections only; source forms never receive synthetic blocks."""
    from api.services.tenders.section_materials import append_section_materials, fill_form_gaps
    from api.services.tenders.pdf_format import toc_block_ids
    demands = [d for d in brief.materialDemands if applicable(d, brief, volume)]
    catalog = {s.key: s for s in slots or []}
    for demand in demands:
        if demand.slotKey not in catalog:
            catalog[demand.slotKey] = PlaceholderItem(
                key=demand.slotKey, title=demand.title, hint=demand.text,
                attachToSection=demand.section)
    fill_form_gaps(doc, brief)
    notes, handled = append_section_materials(
        doc, list(catalog.values()), media, [d.slotKey for d in demands], toc_block_ids(doc),
        auth_enabled=brief.authEnabled)
    for demand in demands:
        # Do not claim an exemption is a generated placeholder.
        if demand.slotKey in handled:
            demand.placements[volume] = 'section-end'
        else:
            demand.placements[volume] = 'source-preserved'
    return notes
