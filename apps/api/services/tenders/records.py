"""投标文件生成记录：入库、列表、详情、审批。"""

from __future__ import annotations

import logging
import hashlib
import traceback
from pathlib import Path
from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.approval_flow import (
    KEY_DONE,
    KEY_SUBMIT,
    default_steps,
    display_step_name,
    find_step,
    get_tender_flow,
    next_step_on_pass,
    public_steps,
    review_steps,
    snapshot_from_steps,
    steps_from_snapshot,
    user_can_act_on_record,
    user_can_act_on_step,
)
from api.services.tenders.assets import tenders_output_dir
from api.services.tenders.generate import generate_bid, generate_pair, generate_volume, volume_pair_status
from api.services.tenders.schema import BidBrief, PlaceholderItem
from common.errors import AppError, ErrorCode
from common.times import to_epoch_ms
from db.models.tender import TenderApprovalLog, TenderRecord
from db.models.user import User

STATUS_DRAFT = "draft"
STATUS_PROCESSING = "processing"
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_SUBMITTED = "submitted"
STATUS_WON = "won"
STATUS_LOST = "lost"
TASK_STATUSES = frozenset(
    {
        STATUS_PROCESSING,
        STATUS_PENDING,
        STATUS_APPROVED,
        STATUS_SUBMITTED,
        STATUS_WON,
        STATUS_LOST,
    }
)
LOCKED_STATUSES = frozenset(
    {STATUS_PENDING, STATUS_APPROVED, STATUS_SUBMITTED, STATUS_WON, STATUS_LOST}
)
APPROVAL_STEPS = tuple(str(item.get("name") or "") for item in default_steps())
REVIEW_STEPS = tuple(str(item.get("name") or "") for item in review_steps(default_steps()))
ACTION_SUBMIT = "submit"
ACTION_PASS = "pass"
ACTION_REJECT = "reject"
ACTION_MARK = "mark"


def infer_project_type(name: str) -> str:
    text = name or ""
    if any(key in text for key in ("数据中心", "机房")):
        return "数据中心"
    if any(key in text for key in ("安防", "监控", "公安")):
        return "安防系统"
    if any(key in text for key in ("园区", "智慧城市", "交通", "综合体")):
        return "智慧园区"
    return "弱电工程"


def deadline_from_brief(brief: BidBrief | dict | None) -> str:
    if brief is None:
        return ""
    raw = brief.bidDate if isinstance(brief, BidBrief) else str(brief.get("bidDate") or "")
    return (raw or "").strip()[:16]


def workflow_locked(status: str | None) -> bool:
    return (status or STATUS_PROCESSING) in LOCKED_STATUSES


def _now() -> datetime:
    return datetime.now(UTC)


def _record_to_dict(
    row: TenderRecord,
    *,
    include_brief: bool = False,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    docx_ok = bool((row.docx_file or "").strip()) and (tenders_output_dir() / row.docx_file).is_file()
    tech_name = (getattr(row, "tech_docx_file", None) or "").strip()
    tech_ok = bool(tech_name) and (tenders_output_dir() / tech_name).is_file()
    pdf_ok = bool(row.pdf_file) and (tenders_output_dir() / str(row.pdf_file)).is_file()
    status = (getattr(row, "status", None) or STATUS_PROCESSING).strip() or STATUS_PROCESSING
    submitted_at = getattr(row, "submitted_at", None)
    decided_at = getattr(row, "decided_at", None)
    snapshot_steps = steps_from_snapshot(getattr(row, "approval_snapshot", None))
    raw_step = getattr(row, "current_step", None) or ""
    current_found = find_step(snapshot_steps, raw_step) if snapshot_steps else None
    data: dict[str, object] = {
        "id": row.public_id,
        "projectName": row.project_name or "",
        "tenderer": row.tenderer or "",
        "bidPriceYuan": float(row.bid_price_yuan or 0),
        "legalPersonName": row.legal_person_name or "",
        "docxFile": row.docx_file,
        "techDocxFile": tech_name or None,
        "pdfFile": row.pdf_file,
        "downloadName": row.download_name or "",
        "techDownloadName": (getattr(row, "tech_download_name", None) or "") or None,
        "pdfDownloadName": row.pdf_download_name,
        "warnings": row.warnings if isinstance(row.warnings, list) else [],
        "username": row.username or "",
        "createdAt": to_epoch_ms(row.created_at),
        "docxAvailable": docx_ok,
        "techDocxAvailable": tech_ok,
        "pdfAvailable": pdf_ok,
        "status": status,
        "deadline": getattr(row, "deadline", None) or "",
        "projectType": getattr(row, "project_type", None) or "",
        "currentStep": display_step_name(snapshot_steps, raw_step),
        "currentStepKey": str((current_found or {}).get("key") or raw_step),
        "approvalSteps": public_steps(snapshot_steps) if snapshot_steps else None,
        "submittedAt": to_epoch_ms(submitted_at) if submitted_at else None,
        "decidedAt": to_epoch_ms(decided_at) if decided_at else None,
        "workflowLocked": workflow_locked(status),
        "userId": int(row.user_id or 0),
    }
    stored = "business"
    if isinstance(row.brief_json, dict):
        v = str(row.brief_json.get("generateVolume") or "")
        if v in {"business", "technical", "full"}:
            stored = v
        demands = row.brief_json.get('materialDemands') or []
        data['materialIntentComplete'] = bool(row.brief_json.get('materialIntentComplete'))
        data['materialCoverage'] = {
            volume:[{key:d.get(key) for key in ('id','title','text','section','level','slotKey','reviewRequired')}
                    | {'status':d['outcomes'][volume],
                       'placement':(d.get('placements') or {}).get(volume,'') if isinstance(d.get('placements'),dict) else ''}
                    for d in demands if isinstance(d,dict)
                    and isinstance(d.get('outcomes'),dict) and d['outcomes'].get(volume) in {'filled','pasted','placeholder'}]
            for volume in ('business','technical','full')
        }
    data.update(volume_pair_status(stored, business_ready=docx_ok, technical_ready=tech_ok))
    from api.services.tenders.qa import qa_summary

    data.update(qa_summary(row.public_id))
    if include_brief and isinstance(row.brief_json, dict):
        data["brief"] = row.brief_json
    if extra:
        data.update(extra)
    return data


def require_format_confirm(brief: BidBrief) -> None:
    if brief.requirementsDriven:
        return
    if (brief.layoutMode or "").strip() != "outline":
        return
    if brief.formatConfirmed:
        return
    raise AppError(
        ErrorCode.VALIDATION,
        "请先预览并确认使用这份格式章模板",
        status_code=422,
    )


def _brief_generate_issues(brief: BidBrief) -> list[str]:
    issues: list[str] = []
    if not (brief.projectName or "").strip():
        issues.append("项目名称")
    if not (brief.tenderer or "").strip():
        issues.append("招标人")
    if not (brief.bidderName or "").strip():
        issues.append("投标人全称")
    if bool(brief.authEnabled) and not (brief.agentName or "").strip():
        issues.append("委托代理人")
    if bool(brief.authEnabled) and not (brief.agentIdNo or "").strip():
        issues.append("代理人身份证号")
    return issues


def _missing_required_titles(
    required_keys: list[str],
    slots: list[PlaceholderItem],
    media: dict,
) -> list[str]:
    titles = {item.key: item.title or item.key for item in slots}
    missing: list[str] = []
    for key in required_keys:
        files = media.get(key) if isinstance(media, dict) else None
        if not files:
            missing.append(titles.get(key) or key)
    return missing


def _missing_slot_warnings(missing: list[str]) -> list[str]:
    return [f'资料库缺少{title}，请在资料库对应项上传' for title in dict.fromkeys(missing)]


def _output_exists(name: str | None) -> bool:
    n = (name or "").strip()
    return bool(n) and (tenders_output_dir() / n).is_file()


async def _word_library_inputs(db, brief):
    from api.services.tenders import library_kb as library
    from api.services.tenders.company_materials import document_matches_company
    from api.services.tenders.categories import item_volume
    from api.services.cablepkg.word_materials import bind
    from common.errors import AppError
    import hashlib

    try:
        base = await library.get_base_by_public_id(db, library.TENDER_LIB_PUBLIC_ID)
    except AppError as exc:
        if exc.status_code != 404:
            raise
        return [], {}
    documents = await library._list_docs(db, base.id)
    by_id = {document.public_id: document for document in documents}
    entries, sources = [], {}
    for document in documents:
        path = library._doc_file_path(document)
        parent = by_id.get(document.parent_id)
        if path is None or path.suffix.lower() not in {'.doc', '.docx'} or parent is None:
            continue
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        eligible = document_matches_company(document, brief, key=library.key_from_tags(parent.tags))
        eligible = eligible and (not document.content_hash or document.content_hash == actual_hash)
        entries.append(dict(documentId=document.public_id, itemId=parent.public_id,
                            title=parent.name, hash=actual_hash, eligible=eligible))
        if eligible:
            sources[document.public_id] = path
    for index, item in enumerate(brief.outlineItems):
        if not item.id:
            item.id = 'section_' + hashlib.sha256((str(index) + item.title).encode()).hexdigest()[:20]
    outline = [dict(item.model_dump(), volume=item_volume(kind=item.kind, title=item.title, key=item.id))
               for item in brief.outlineItems]
    from api.services.tenders.pdf_format import _word_format_file
    from api.services.cablepkg.word_materials import _region, text
    from docx import Document
    original = _word_format_file(brief.invitationId)
    if original is not None:
        template = Document(original)
        from api.services.cablepkg.word_materials import section_key, _toc
        from api.services.tenders.schema import OutlineItem
        from api.services.tenders.outline import classify_kind
        from docx.oxml.ns import qn
        import re
        represented = {section_key(item['rawTitle'] or item['title']) for item in outline}
        source_keys = {section_key(entry['title']) for entry in entries if entry['eligible']}
        for index, node in enumerate(template.element.body):
            if node.tag != qn('w:p') or _toc(node):
                continue
            heading = text(node)
            key = section_key(heading)
            if (re.match(r'^\s*\d+[.．、]', heading) and len(heading) <= 255
                    and key in source_keys and key not in represented):
                ident = 'source_' + hashlib.sha256((brief.invitationId + str(index) + heading).encode()).hexdigest()[:20]
                item = OutlineItem(id=ident, title=heading, rawTitle=heading, kind=classify_kind(heading), source='copy', tocEligible=False)
                brief.outlineItems.append(item)
                outline.append(dict(item.model_dump(), volume=item_volume(kind=item.kind, title=item.title, key=item.id)))
                represented.add(key)
        titles = [item['rawTitle'] or item['title'] for item in outline]
        for item in outline:
            if '承诺函' in item['title']:
                try:
                    _, members = _region(template, item['rawTitle'] or item['title'], titles)
                    item['context'] = ''.join(text(node) for node in members)[:1000]
                except ValueError:
                    pass
    bindings, review = bind(outline, entries, [b.model_dump() for b in brief.libraryBindings])
    from api.services.tenders.schema import WordMaterialBinding
    # Retain stale snapshots so retrying cannot silently adopt a changed source version.
    retained = {binding.targetId: binding for binding in brief.libraryBindings}
    retained.update({binding['targetId']: WordMaterialBinding.model_validate(binding) for binding in bindings})
    brief.libraryBindings = list(retained.values())
    brief.libraryBindingReview = review[:400]
    return bindings, sources


async def _generate_for_record(
    db: AsyncSession,
    brief: BidBrief,
    *,
    peer_ready: bool = False,
    material_owner_id: int | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    from api.services.tenders.demo import build_demo_output, demo_enabled

    if demo_enabled():
        generated = await build_demo_output(brief)
        empty = {"matched": [], "missingFiles": [], "createdItems": []}
        return generated, empty
    extra = {'material_owner_id': material_owner_id} if material_owner_id is not None else {}
    return await _prepare_and_generate(db, brief, peer_ready=peer_ready, **extra)


async def _prepare_scoring_drafts(db, brief, facts, *, client=None):
    """Generate scoring prose while the record layer still owns the database session."""
    from api.services.tenders.assets import load_invitation_text
    from api.services.tenders.generation_parallel import ordered_generation
    from api.services.tenders.scoring_points import read_scoring_points
    from api.services.models.runtime import build_llm_client

    invitation = load_invitation_text(brief.invitationId)
    points = read_scoring_points(invitation, notes=[])
    if not points:
        print("记录层无评分项，跳过预生成")
        return {}
    if client is None:
        client = await build_llm_client(db, "pro")
    print("评分叙述模型客户端：", type(client).__name__)
    from api.services.tenders.tender_facts import extract_tender_facts

    detail = extract_tender_facts(invitation)
    tender = detail.get("facts") or {}
    fact_map = {}
    for key, attr in (
        ("招标范围", "bidContent"), ("质量标准", "quality"),
        ("计划工期", "deliveryDays"), ("投标有效期", "bidValidityDays"),
        ("投标保证金", "bidBondYuan"),
    ):
        value = getattr(brief, attr, None)
        if value not in (None, "", 0, 0.0):
            fact_map[key] = value
    if "项目名称" not in fact_map:
        match = re.search(r"项目名称\s*[：:]\s*([^\r\n；;，,]+)", invitation)
        if match and match.group(1).strip():
            fact_map["项目名称"] = match.group(1).strip()
    if "项目名称" not in fact_map:
        value = str(tender.get("项目名称") or "").strip()
        if value:
            fact_map["项目名称"] = value
    for key in ("建设地点", "资金来源", "安全要求", "工程概况", "招标范围", "质量标准", "计划工期"):
        value = str(tender.get(key) or "").strip()
        if len(value) > 80:
            value = re.split(r"[。；，,]", value, maxsplit=1)[0].strip()
        if value and value not in {"无", "见招标文件", "/", "—"}:
            fact_map.setdefault(key, value)
    if not tender:
        print("邀请书里没有可识别的项目信息：", detail.get("notes") or [])
    async def generate(point):
        prompt = (
            "本项目事实：\n" + json.dumps(fact_map, ensure_ascii=False) +
            "\n评分项：" + point.item +
            "\n评分细则原文：" + point.criterion +
            "\n所属章节：" + point.target_section +
            "\n分值：" + str(point.item_score_min) + " / " + str(point.item_score_max) +
            "\n要求：第一段承接事实并使用可用的项目名称与建设地点；第二段对应评分细则动宾结构；"
            "第三段只使用事实中的数字进行量化安排；第四段用我方表态收口。全文四段、段间空行、180至420字。"
            "禁止虚构人员姓名、证书编号、业绩数量、财务数字；未提供信息留空，不写无、待补充、待核实；评分项名在同一句不得重复两次以上。"
        )
        raw = await client.complete([{"role": "user", "content": prompt}], mode="pro")
        return point.item, str(raw or "").strip()
    results = await ordered_generation([lambda point=point: generate(point) for point in points],
                                       stage="scoring_narrative", labels=[p.item for p in points])
    print("评分叙述模型返回：", len(results), "非空：", sum(bool(body) for _, body in results))
    return {item: body for item, body in results if item and body}


async def _prepare_and_generate(
    db: AsyncSession,
    brief: BidBrief,
    *,
    peer_ready: bool = False,
    material_owner_id: int | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    """跑生成管道，返回 (generated, attachment_match)。会就地补全 brief 的槽位字段。"""
    import time

    from api.services.tenders.extract import merge_performance_lines
    from api.services.tenders.library_kb import (
        catalog_placeholders,
        count_uncached_performance_files,
        filter_legal_id_attachments,
        filter_performance_attachments,
        group_performance_attachments,
        legal_persons_from_library,
        library_facts,
        list_library_items,
        performance_from_library,
        resolve_attachments,
    )
    from api.services.tenders.placeholders import CRITICAL_SLOT_KEYS, collect_slots, this_bid_keys

    t0 = time.perf_counter()
    source_tech_ids = {item.id for item in brief.outlineItems if item.kind in {'tech_plan', 'tech_chapter'} and not item.skipped}
    from api.services.tenders.quote import validate_quote_source
    from api.services.tenders.requirement_bid import prepare_requirements_bid

    from api.services.tenders.company_materials import library_bidder_name
    if not brief.bidderName.strip():
        brief.bidderName = await library_bidder_name(db)
    quote_notes = validate_quote_source(brief)
    from api.services.tenders.company_materials import scope_performance_lines

    brief.performanceLines, perf_notes = scope_performance_lines(brief)
    quote_notes.extend(perf_notes)
    requirement_notes = prepare_requirements_bid(brief)
    if not (brief.bidDate or "").strip():
        brief.bidDate = date.today().isoformat()
    if brief.authEnabled:
        from api.services.tenders.history import latest_agent_profile

        hint = await latest_agent_profile(db, bidder_name=brief.bidderName)
        if not (brief.agentName or "").strip() and hint.get("agentName"):
            brief.agentName = hint["agentName"]
        if not (brief.agentIdNo or "").strip() and hint.get("agentIdNo"):
            brief.agentIdNo = hint["agentIdNo"]
    issues = _brief_generate_issues(brief)
    if issues:
        raise AppError(
            ErrorCode.VALIDATION,
            ('清单与本次标书不匹配，未采用。' if brief.quoteMatchStatus == 'unmatched' else '')
            + "请先完善：" + "、".join(issues),
            status_code=422,
        )
    require_format_confirm(brief)

    if brief.bidTableImportId:
        from api.services.bid_table_import import load_import
        try:
            imported = load_import(brief.bidTableImportId)
        except (ValueError, OSError) as exc:
            raise AppError(ErrorCode.VALIDATION, '已确认填报数据无法读取，请重新导入确认', status_code=422) from exc
        if imported.get('owner') != material_owner_id or not imported.get('confirmed') or imported.get('invitationId') != brief.invitationId:
            raise AppError(ErrorCode.VALIDATION, 'Invalid confirmed table import for this task', status_code=422)
        from decimal import Decimal
        imported_quotes = [r for r in imported['rows'] if r['kind'] == 'quote']
        if imported_quotes:
            total = sum((Decimal(r['values'][9]) for r in imported_quotes), Decimal(0))
            if brief.bidPriceYuan and abs(Decimal(str(brief.bidPriceYuan)) - total) > Decimal('0.01'):
                raise AppError(ErrorCode.VALIDATION, '投标总价与已确认填报数据不一致，请核对价格或重新导入确认', status_code=422)
            brief.bidPriceYuan = float(total)

    from api.services.tenders.outline import classify_kind
    for item in brief.outlineItems:
        corrected = classify_kind(item.rawTitle or item.title)
        if item.draftStatus not in {'manual', 'edited'} and (item.kind == 'unknown' or '投标人基本情况表' in item.title):
            item.kind = corrected
    catalog = await catalog_placeholders(db)
    word_bindings, word_sources = await _word_library_inputs(db, brief)
    from api.services.tenders.material_intents import recognize_material_demands

    material_intent_notes = await recognize_material_demands(brief,catalog=catalog,db=db)
    t_catalog = time.perf_counter()
    lib_perf = await performance_from_library(db, extract_missing=False, bidder_name=brief.bidderName)
    pending_perf = await count_uncached_performance_files(db)
    if lib_perf:
        brief.performanceLines = merge_performance_lines(
            brief.performanceLines,
            lib_perf,
            requirement=brief.performanceRequirement,
            preserve_flags=True,
        )
    from api.services.tenders.performance import pad_performance_placeholders

    brief.performanceLines = pad_performance_placeholders(
        brief.performanceLines,
        brief.performanceRequirement,
    )
    has_agent = bool(brief.authEnabled)
    from api.services.tenders.requirement_bid import invitation_text

    required_keys, include_keys = this_bid_keys(
        extra=brief.extraPlaceholders,
        catalog=catalog,
        required_keys=brief.requiredSlotKeys,
        include_keys=brief.includeSlotKeys,
        has_agent=has_agent,
        outline_items=brief.outlineItems,
        requirements=brief.bidRequirements,
        source_text=invitation_text(brief),
        invitation_id=brief.invitationId,
        material_demands=brief.materialDemands,
    )
    brief.requiredSlotKeys = required_keys
    brief.includeSlotKeys = include_keys
    slots = collect_slots(brief.extraPlaceholders, catalog=catalog, include_keys=include_keys,
                          outline_items=brief.outlineItems)
    media = await resolve_attachments(db, slots)
    from api.services.tenders.slots import attach_invitation_drawings

    media = attach_invitation_drawings(media, brief.invitationId)
    from api.services.tenders.company_materials import scoped_library_media
    media, company_file_ids, company_person_keys, company_notes = await scoped_library_media(db, brief, media)
    material_notes, reference_ids = [], []
    if any(c.enabled for c in brief.materialSelections.values()):
        from api.services.tenders.material_store import generation_inputs

        if material_owner_id is None:
            raise AppError(ErrorCode.VALIDATION, '新资料缺少所属用户，无法参与生成', status_code=422)
        slots, media, material_notes, reference_ids = generation_inputs(
            material_owner_id, brief, slots, media)
    from api.services.tenders.performance import bid_performance_lines

    if "perf" in media:
        selected = bid_performance_lines(brief.performanceLines)
        grouped = await group_performance_attachments(db, media["perf"], selected)
        media["perf"] = await filter_performance_attachments(db, media["perf"], selected)
        if not media["perf"]:
            media.pop("perf", None)
        for name_key, files in grouped.items():
            if files:
                media[f"perf:{name_key}"] = files
    persons = await legal_persons_from_library(db, extract_missing=False)
    all_persons = persons
    # These IDs describe company-scoped documents, not the stale person choice.
    # Legal representative is an explicit person choice, independent of the
    # bidder company. Company scoping remains enforced for corporate materials;
    # the selected person's ID scans are filtered by person key below.
    persons = list(all_persons)
    logging.getLogger(__name__).info('legal rebind candidates before=%s after=%s company_person_keys=%s picked=%s',
        [{'key':p.get('key'),'name':p.get('name')} for p in all_persons],
        [{'key':p.get('key'),'name':p.get('name')} for p in persons],sorted(str(k) for k in company_person_keys),brief.legalPersonKey)
    picked = (brief.legalPersonKey or "").strip()
    known = {str(p.get('key') or '').strip() for p in persons}
    number = (brief.legalPersonIdNo or '').strip().upper()
    selected = None
    if picked not in known:
        if not number:
            logging.getLogger(__name__).warning(
                'legal selection missing idNo picked=%s persons=%s reason=%s',
                picked, [{'key':p.get('key'), 'name':p.get('name'),
                          'hasIdNo':bool(p.get('idNo'))} for p in persons],
                'stale_key' if persons else 'no_company_persons')
        candidates = []
        if number:
            candidates = [p for p in persons if str(p.get('idNo') or '').strip().upper()==number]
        if not candidates:
            name = ''.join((brief.legalPersonName or '').split())
            if name:
                candidates = [p for p in persons if ''.join(str(p.get('name') or '').split())==name]
        if len(candidates)==1:
            selected=candidates[0]
        elif len(persons)==1:
            selected=persons[0]
        if selected is not None:
            picked=str(selected.get('key') or '').strip()
            brief.legalPersonKey=picked
            for field, attr in [('name','legalPersonName'), ('gender','legalPersonGender'),
                                ('age','legalPersonAge'), ('idNo','legalPersonIdNo')]:
                setattr(brief,attr,str(selected.get(field) or ''))
    if len(persons)>1 and not picked:
        raise AppError(ErrorCode.VALIDATION,
                       '资料库有多位法定代表人，请先选择本标使用的人',status_code=422)
    if picked:
        known = {str(item.get("key") or "") for item in persons}
        if picked not in known:
            raise AppError(
                ErrorCode.VALIDATION,
                "所选法定代表人不在资料库，请重新选择",
                status_code=422,
            )
        if "id_legal" in media:
            media["id_legal"] = await filter_legal_id_attachments(db, media["id_legal"], picked)
            if not media["id_legal"]:
                media.pop("id_legal", None)
    checked_keys = list(dict.fromkeys([
        *required_keys, *(slot.key for slot in slots if slot.key in CRITICAL_SLOT_KEYS),
    ]))
    missing = _missing_required_titles(checked_keys, slots, media)
    vol = generate_volume(brief)
    both = bool(brief.generateBoth)
    if vol in {"business", "technical"} and not both:
        from api.services.tenders.categories import slot_volume

        keep = []
        titles = {item.title or item.key: item.key for item in slots}
        for title in missing:
            key = titles.get(title) or title
            if slot_volume(key, title) == vol:
                keep.append(title)
        missing = keep
    t_prep = time.perf_counter()
    facts = [f for f in await library_facts(db) if f.get("fileId") in company_file_ids and f.get("fileId") not in word_sources]
    draft_notes: list[str] = []
    scoring_drafts = {}
    if both or vol in {"business", "full"}:
        print("评分叙述预生成条件：", {"vol": vol, "both": both})
        try:
            scoring_drafts = await _prepare_scoring_drafts(db, brief, facts)
            print(f"记录层评分叙述预生成写入 {len(scoring_drafts)} 条")
        except Exception as exc:  # model failure leaves downstream fallback intact
            print("评分叙述预生成异常类型：", type(exc).__name__)
            traceback.print_exc()
            draft_notes.append(f"评分叙述预生成失败，已走降级：{exc}")
    technical_package_active = False
    if both or vol == 'technical':
        from api.services.tenders.technical_package import draft_package

        package_brief=brief.model_copy(deep=True) if reference_ids else brief
        if reference_ids:
            package_brief.techPlanSourceIds=[*brief.techPlanSourceIds,*reference_ids]
        package, package_notes = await draft_package(db, package_brief)
        if package:
            brief.technicalPackage = package
            draft_notes.extend(package_notes)
            technical_package_active = True
    needs_tech_body = any(
        not item.skipped and item.id in source_tech_ids and item.kind in {'tech_plan', 'tech_chapter'}
        and (item.draftStatus or '').lower() not in {'manual', 'edited'}
        and (not (item.draftBody or '').strip() or '未完整生成' in (item.draftBody or ''))
        for item in brief.outlineItems
    )
    if brief.techPlanSourceIds and not technical_package_active and not (brief.autoDraftTechPlan
                                        and (both or vol in {'technical', 'full'})):
        from api.services.tenders.gen_tech_plan import _user_sources

        _user_sources(brief.techPlanSourceIds, brief=brief, notes=draft_notes)
        draft_notes.append('本次未启用技术方案自动起草，上传技术资料未送入模型')
    if not technical_package_active and (needs_tech_body or (getattr(brief, "autoDraftTechPlan", False) and (both or vol in {"technical", "full"}))):
        from api.services.tenders.gen_tech_plan import draft_technical_plan

        draft_brief = brief.model_copy(deep=True) if reference_ids else brief
        if reference_ids:
            draft_brief.techPlanSourceIds = [*brief.techPlanSourceIds, *reference_ids]
        prepared, draft_notes = await draft_technical_plan(db, draft_brief)
        brief.outlineItems = prepared.outlineItems
        brief.deviationLines = prepared.deviationLines
        brief.techScorecard = prepared.techScorecard
    if getattr(brief, "autoDraftBizResp", False) and (both or vol in {"business", "full"}):
        from api.services.tenders.gen_biz_resp import draft_business_response

        prepared, biz_notes = await draft_business_response(
            db,
            brief,
            library_facts=facts,
            kb_ids=getattr(brief, "bizRespKbIds", []),
        )
        brief.outlineItems = prepared.outlineItems
        brief.bizDeviationLines = prepared.bizDeviationLines
        brief.bizDevHeaders = prepared.bizDevHeaders
        draft_notes.extend(biz_notes)
    media = {key: [path for path in files if Path(path).suffix.lower() not in {'.doc', '.docx'}]
             for key, files in media.items()}
    catalog_facts = list(facts)
    if scoring_drafts:
        catalog_facts.append({"_scoring_drafts": scoring_drafts})
    gen_kw = dict(catalog_slots=slots, catalog_media=media, catalog_facts=catalog_facts)
    if word_bindings:
        gen_kw.update(word_bindings=word_bindings, word_sources=word_sources)
    if both:
        generated = generate_pair(brief, **gen_kw)
    else:
        generated = generate_bid(brief, **gen_kw)
    t_docx = time.perf_counter()
    from api.services.tenders.categories import business_soft_issues, technical_soft_issues

    word_binding_notes = ['Word 资料绑定待确认：' + str(item.get('targetId') or '') + '；' + item['reason']
                          for item in brief.libraryBindingReview if item.get('status') == 'needs_review']
    warnings = list(dict.fromkeys([
        *word_binding_notes, *quote_notes, *requirement_notes, *company_notes, *material_notes, *material_intent_notes, *draft_notes, *(generated.get("warnings") or []),
    ]))
    if pending_perf and vol != "technical":
        warnings.append(
            f"类似业绩有 {pending_perf} 个合同/发票尚未抽出摘要，本次未做 OCR（避免拖慢生成）。"
            "可在资料库重新上传或稍后补抽后再生成。"
        )
    for note in _missing_slot_warnings(missing):
        if note not in warnings:
            warnings.append(note)
    for note in technical_soft_issues(
        brief, slots=slots, media=media, required_keys=required_keys
    ):
        text = str(note)
        if not both and vol == "business" and text.startswith("技术标"):
            continue
        if not both and vol == "technical" and text.startswith("商务标"):
            continue
        if note not in warnings:
            warnings.append(note)
    if getattr(brief, "autoDraftBizResp", False) and (both or vol in {"business", "full"}):
        for note in business_soft_issues(brief):
            if note not in warnings:
                warnings.append(note)
    library_items = [
        {
            "key": item.key,
            "title": item.title,
            "hint": item.hint,
            "fileCount": len(media.get(item.key) or []),
            "files": [],
        }
        for item in collect_slots(catalog=catalog)
    ]
    from api.services.tenders.match import attachment_match_notes, build_attachment_match

    attachment_match = build_attachment_match(
        library_items or await list_library_items(db),
        required_keys=required_keys,
        include_keys=include_keys,
    )
    from api.services.cablepkg.word_materials import reconcile_match
    attachment_match = reconcile_match(attachment_match, generated.get('wordMaterialOutcomes') or [], word_bindings)
    for note in attachment_match_notes(attachment_match):
        if note not in warnings:
            warnings.append(note)
    elapsed = time.perf_counter() - t0
    warnings.append(
        f"生成耗时约 {elapsed:.1f}s"
        f"（资料 {t_catalog - t0:.1f}s / 准备 {t_prep - t_catalog:.1f}s / Word {t_docx - t_prep:.1f}s）"
    )
    if both:
        biz_ready = bool(generated.get("docxFile"))
        tech_ready = bool(generated.get("techDocxFile"))
        status_vol = "business"
    else:
        biz_ready = bool(generated.get("docxFile")) or (vol == "technical" and peer_ready)
        tech_ready = bool(generated.get("techDocxFile")) or (vol == "business" and peer_ready)
        status_vol = vol
    status = volume_pair_status(status_vol, business_ready=biz_ready, technical_ready=tech_ready)
    generated.update(status)
    note = str(status.get("otherVolumeNote") or "")
    if note and note not in warnings:
        warnings.append(note)
    from api.services.tenders.expiry_check import generation_expiry_warnings
    warnings.extend(await generation_expiry_warnings(db, attachment_match, media))
    from api.services.cablepkg.word_materials import section_key
    placed_word_titles = {section_key(row['targetTitle']) for row in generated.get('wordMaterialOutcomes', []) if row['status'] in {'copied', 'filled'}}
    warnings = [warning for warning in warnings if not (isinstance(warning, str)
                and warning.startswith('资料库缺少') and section_key(warning.removeprefix('资料库缺少').split('，请')[0]) in placed_word_titles)]
    generated["warnings"] = warnings
    attachment_match['wordMaterials'] = generated.get('wordMaterialOutcomes') or []
    return generated, attachment_match


def _apply_generated_files(row: TenderRecord, brief: BidBrief, generated: dict[str, object]) -> None:
    row.project_name = (brief.projectName or "").strip()[:256]
    row.tenderer = (brief.tenderer or "").strip()[:256]
    row.bid_price_yuan = float(brief.bidPriceYuan or 0)
    row.legal_person_name = (brief.legalPersonName or "").strip()[:64]
    vol = generate_volume(brief)
    both = bool(brief.generateBoth)
    biz_file = generated.get("docxFile")
    if biz_file:
        row.docx_file = str(biz_file)
        row.download_name = str(generated.get("downloadName") or "")
    if vol == "full" and not both:
        row.tech_docx_file = None
        row.tech_download_name = None
    tech_file = generated.get("techDocxFile")
    if tech_file:
        row.tech_docx_file = str(tech_file)
        row.tech_download_name = generated.get("techDownloadName") or None
    if both or vol != "technical":
        row.pdf_file = generated.get("pdfFile") or None
        row.pdf_download_name = generated.get("pdfDownloadName") or None
    row.warnings = list(generated.get("warnings") or [])
    row.brief_json = brief.model_dump(mode="json")
    row.deadline = deadline_from_brief(brief)
    if not (row.project_type or "").strip():
        row.project_type = infer_project_type(row.project_name)


def _unlink_output(name: str | None) -> None:
    if not name:
        return
    out_dir = tenders_output_dir()
    path = (out_dir / str(name)).resolve()
    try:
        if path.is_file() and path.is_relative_to(out_dir.resolve()):
            path.unlink(missing_ok=True)
    except OSError:
        pass
    if str(name).lower().endswith(".docx"):
        from api.services.tenders.preview_pdf import unlink_preview_pdf

        unlink_preview_pdf(str(name))


async def create_record_from_generate(
    db: AsyncSession,
    brief: BidBrief,
    *,
    user_id: int,
    username: str,
) -> dict[str, object]:
    from api.services.tenders.gen_tech_plan import _source_file_limit
    from common.config import get_settings

    try:
        _source_file_limit(get_settings(), strict=True)
        return await _create_record_from_generate(db, brief, user_id=user_id, username=username)
    except (AttributeError, TypeError) as exc:
        import logging

        logging.getLogger(__name__).exception("生成入口遇到配置或代码不兼容")
        raise AppError(ErrorCode.INTERNAL, "服务端配置未生效，请重启后端", status_code=503) from exc


async def _create_record_from_generate(
    db: AsyncSession,
    brief: BidBrief,
    *,
    user_id: int,
    username: str,
) -> dict[str, object]:
    """生成 Word。未到下载定稿前记为草稿，不出现在生成记录里。"""
    extra = {'material_owner_id': user_id} if brief.bidTableImportId or any(c.enabled for c in brief.materialSelections.values()) else {}
    generated, attachment_match = await _generate_for_record(db, brief, **extra)
    # 没走到下载定稿前只留草稿，列表里看不到；换一份时清掉本人旧草稿
    await _drop_user_drafts(db, user_id)
    row = TenderRecord(
        public_id=uuid4().hex[:16],
        user_id=user_id,
        username=(username or "").strip()[:64],
        status=STATUS_DRAFT,
        current_step="",
        project_type=infer_project_type((brief.projectName or "").strip()),
        deadline=deadline_from_brief(brief),
        docx_file=str(generated.get("docxFile") or ""),
        download_name=str(generated.get("downloadName") or ""),
    )
    _apply_generated_files(row, brief, generated)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    from api.services.tenders.bid_archive import schedule_archive

    schedule_archive(int(row.id))
    out = _record_to_dict(row)
    out["warnings"] = generated.get("warnings") or []
    out["defaultsUsed"] = generated.get("defaultsUsed")
    out["imageQuality"] = generated.get("imageQuality") or {}
    out["wordMaterialOutcomes"] = generated.get("wordMaterialOutcomes") or []
    out["libraryBindings"] = [b.model_dump() for b in brief.libraryBindings]
    out["libraryBindingReview"] = brief.libraryBindingReview
    out["attachmentMatch"] = attachment_match
    return out


async def _drop_user_drafts(db: AsyncSession, user_id: int) -> None:
    rows = (
        await db.execute(
            select(TenderRecord).where(
                TenderRecord.user_id == int(user_id),
                TenderRecord.status == STATUS_DRAFT,
            )
        )
    ).scalars().all()
    from api.services.tenders.bid_archive import forget_record

    for row in rows:
        _purge_record_files(row)
        await forget_record(db, int(row.id))
        await db.delete(row)
    if rows:
        await db.flush()


async def publish_record(
    db: AsyncSession,
    public_id: str,
    *,
    user: User,
    admin: bool,
) -> dict[str, object]:
    """走到下载定稿后，草稿才进入生成记录。已入库的再点一次不动。"""
    row = await _get_row(db, public_id)
    _assert_owner_or_admin(row, user, admin=admin)
    if (row.status or "") == STATUS_DRAFT:
        row.status = STATUS_PROCESSING
        await db.commit()
        await db.refresh(row)
    return _record_to_dict(row)


def _filter_clause(
    *,
    q: str | None,
    status: str | None,
    owner: str | None,
    user_id: int | None,
    project_type: str | None,
):
    clauses = []
    keyword = (q or "").strip()
    if keyword:
        like = f"%{keyword}%"
        clauses.append(
            or_(
                TenderRecord.project_name.like(like),
                TenderRecord.tenderer.like(like),
                TenderRecord.username.like(like),
            )
        )
    if status and status in TASK_STATUSES:
        clauses.append(TenderRecord.status == status)
    if owner and owner.strip():
        clauses.append(TenderRecord.username == owner.strip())
    if user_id is not None:
        clauses.append(TenderRecord.user_id == int(user_id))
    if project_type and project_type.strip():
        clauses.append(TenderRecord.project_type == project_type.strip())
    if not clauses:
        return None
    return and_(*clauses) if len(clauses) > 1 else clauses[0]


async def list_records(
    db: AsyncSession,
    *,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    owner: str | None = None,
    user_id: int | None = None,
    project_type: str | None = None,
    record_ids: list[int] | None = None,
    submitted_only: bool = False,
    pending_for: User | None = None,
) -> dict[str, object]:
    clause = _filter_clause(
        q=q, status=status, owner=owner, user_id=user_id, project_type=project_type
    )
    base = select(func.count()).select_from(TenderRecord)
    stmt = select(TenderRecord).order_by(TenderRecord.id.desc())
    if clause is not None:
        base = base.where(clause)
        stmt = stmt.where(clause)
    # 草稿还没到下载定稿，不进生成记录 / 工作任务
    base = base.where(TenderRecord.status != STATUS_DRAFT)
    stmt = stmt.where(TenderRecord.status != STATUS_DRAFT)
    if submitted_only:
        base = base.where(TenderRecord.submitted_at.is_not(None))
        stmt = stmt.where(TenderRecord.submitted_at.is_not(None))
    if record_ids is not None:
        if not record_ids:
            return {"total": 0, "items": []}
        base = base.where(TenderRecord.id.in_(record_ids))
        stmt = stmt.where(TenderRecord.id.in_(record_ids))
    if pending_for is not None:
        rows = (await db.execute(stmt.limit(500))).scalars().all()
        if not getattr(pending_for, "is_superuser", False):
            rows = [
                row
                for row in rows
                if user_can_act_on_record(
                    pending_for,
                    getattr(row, "approval_snapshot", None),
                    getattr(row, "current_step", None),
                )
            ]
        total = len(rows)
        start = max(0, offset)
        end = start + max(1, min(limit, 200))
        rows = rows[start:end]
    else:
        total = int((await db.execute(base)).scalar_one() or 0)
        rows = (
            await db.execute(stmt.offset(max(0, offset)).limit(max(1, min(limit, 200))))
        ).scalars().all()
    extras = await _list_log_extras(db, [int(r.id) for r in rows])
    return {
        "total": total,
        "items": [_record_to_dict(r, extra=extras.get(int(r.id))) for r in rows],
    }


async def _list_log_extras(
    db: AsyncSession, record_ids: list[int]
) -> dict[int, dict[str, object]]:
    if not record_ids:
        return {}
    rows = (
        await db.execute(
            select(TenderApprovalLog)
            .where(TenderApprovalLog.record_id.in_(record_ids))
            .order_by(TenderApprovalLog.id.desc())
        )
    ).scalars().all()
    extras: dict[int, dict[str, object]] = {}
    for log in rows:
        rid = int(log.record_id)
        if rid in extras:
            continue
        extras[rid] = {
            "lastAction": log.action,
            "lastComment": log.comment or "",
            "lastActor": log.username or "",
        }
    return extras


async def get_record(db: AsyncSession, public_id: str) -> dict[str, object]:
    row = await _get_row(db, public_id)
    rid = int(row.id) if getattr(row, "id", None) is not None else 0
    extras = await _list_log_extras(db, [rid] if rid else [])
    log_rows = list(getattr(row, "approval_logs", None) or [])
    logs = [
        {
            "id": int(item.id),
            "action": item.action,
            "step": item.step,
            "comment": item.comment or "",
            "username": item.username or "",
            "createdAt": to_epoch_ms(item.created_at),
        }
        for item in sorted(log_rows, key=lambda x: int(x.id or 0))
    ]
    extra = dict(extras.get(rid) or {})
    extra["approvalLogs"] = logs
    return _record_to_dict(row, include_brief=True, extra=extra)


async def _get_row(db: AsyncSession, public_id: str) -> TenderRecord:
    pid = (public_id or "").strip()
    if not pid:
        raise AppError(ErrorCode.BAD_REQUEST, "missing record id", status_code=400)
    row = (
        await db.execute(select(TenderRecord).where(TenderRecord.public_id == pid))
    ).scalar_one_or_none()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "tender record not found", status_code=404)
    return row


def _assert_owner_or_admin(row: TenderRecord, user: User, *, admin: bool) -> None:
    if admin:
        return
    if int(row.user_id) != int(user.id):
        raise AppError(ErrorCode.FORBIDDEN, "只能操作本人的投标任务", status_code=403)


def _purge_record_files(row: TenderRecord) -> list[str]:
    removed: list[str] = []
    for name in (row.docx_file, getattr(row, "tech_docx_file", None), row.pdf_file):
        if name:
            _unlink_output(str(name))
            removed.append(str(name))
    from api.services.tenders.qa import unlink_qa_report

    unlink_qa_report(row.public_id)
    return removed


async def delete_record(
    db: AsyncSession,
    public_id: str,
    *,
    user: User,
    admin: bool,
) -> dict[str, object]:
    """删除生成记录，并尽量清理对应 Word / 资质 PDF 文件。"""
    row = await _get_row(db, public_id)
    if admin:
        pass
    else:
        _assert_owner_or_admin(row, user, admin=False)
        if workflow_locked(row.status):
            raise AppError(ErrorCode.VALIDATION, "审批中或已定稿的任务不能删除", status_code=422)
    from api.services.tenders.bid_archive import forget_record

    removed = _purge_record_files(row)
    payload = _record_to_dict(row)
    await forget_record(db, int(row.id))
    await db.delete(row)
    await db.commit()
    return {"deleted": True, "id": payload["id"], "removedFiles": removed}


async def clear_mine_records(
    db: AsyncSession,
    *,
    user: User,
    admin: bool,
) -> dict[str, object]:
    """清空当前用户自己的生成记录。管理员可删本人名下含审批中的任务。"""
    rows = (
        (await db.execute(select(TenderRecord).where(TenderRecord.user_id == int(user.id))))
        .scalars()
        .all()
    )
    from api.services.tenders.bid_archive import forget_record

    deleted = 0
    skipped = 0
    removed_files: list[str] = []
    ids: list[str] = []
    for row in rows:
        if not admin and workflow_locked(row.status):
            skipped += 1
            continue
        removed_files.extend(_purge_record_files(row))
        ids.append(str(row.public_id))
        await forget_record(db, int(row.id))
        await db.delete(row)
        deleted += 1
    await db.commit()
    return {
        "deleted": deleted,
        "skipped": skipped,
        "ids": ids,
        "removedFiles": removed_files,
    }


async def regenerate_from_record(
    db: AsyncSession,
    public_id: str,
    *,
    user: User,
    admin: bool,
    brief: BidBrief | None = None,
) -> dict[str, object]:
    """用当前表单（或记录快照）覆盖同一条记录的 Word，不改工作流状态。"""
    row = await _get_row(db, public_id)
    _assert_owner_or_admin(row, user, admin=admin)
    if workflow_locked(row.status):
        raise AppError(ErrorCode.VALIDATION, "当前状态不可重新生成，请先驳回或解锁", status_code=422)
    if brief is None:
        if not isinstance(row.brief_json, dict) or not row.brief_json:
            raise AppError(ErrorCode.VALIDATION, "该记录没有保存表单，无法重新生成", status_code=422)
        try:
            brief = BidBrief.model_validate(row.brief_json)
        except Exception as exc:
            raise AppError(ErrorCode.VALIDATION, f"记录表单无法解析：{exc}", status_code=422) from exc
    if 'libraryBindings' not in brief.model_fields_set and isinstance(row.brief_json, dict):
        previous = BidBrief.model_validate(row.brief_json)
        if previous.invitationId == brief.invitationId:
            brief.libraryBindings = previous.libraryBindings
    old_docx, old_tech, old_pdf = row.docx_file, getattr(row, "tech_docx_file", None), row.pdf_file
    vol = generate_volume(brief)
    both = bool(brief.generateBoth)
    if both:
        peer_ready = False
    elif vol == "technical":
        peer_ready = _output_exists(old_docx)
    else:
        peer_ready = _output_exists(old_tech)
    extra = {'material_owner_id': int(row.user_id)} if brief.bidTableImportId or any(c.enabled for c in brief.materialSelections.values()) else {}
    generated, attachment_match = await _generate_for_record(db, brief, peer_ready=peer_ready, **extra)
    _apply_generated_files(row, brief, generated)
    await db.commit()
    await db.refresh(row)
    if (both or vol != "technical") and old_docx and old_docx != row.docx_file:
        _unlink_output(old_docx)
    if (both or vol != "business") and old_tech and old_tech != getattr(row, "tech_docx_file", None):
        _unlink_output(str(old_tech))
    if (both or vol != "technical") and old_pdf and old_pdf != row.pdf_file:
        _unlink_output(str(old_pdf))
    from api.services.tenders.qa import unlink_qa_report

    if both:
        unlink_qa_report(row.public_id, volume="business")
        unlink_qa_report(row.public_id, volume="technical")
    else:
        unlink_qa_report(row.public_id, volume=vol)
    from api.services.tenders.bid_archive import schedule_archive

    schedule_archive(int(row.id), volumes=["business", "technical"] if both else [vol])
    out = _record_to_dict(row)
    out["warnings"] = generated.get("warnings") or []
    out["defaultsUsed"] = generated.get("defaultsUsed")
    out["imageQuality"] = generated.get("imageQuality") or {}
    out["wordMaterialOutcomes"] = generated.get("wordMaterialOutcomes") or []
    out["libraryBindings"] = [b.model_dump() for b in brief.libraryBindings]
    out["libraryBindingReview"] = brief.libraryBindingReview
    out["attachmentMatch"] = attachment_match
    return out


async def _add_log(
    db: AsyncSession,
    row: TenderRecord,
    *,
    user: User,
    action: str,
    step: str,
    comment: str = "",
) -> None:
    db.add(
        TenderApprovalLog(
            record_id=int(row.id),
            user_id=int(user.id),
            username=(user.username or "").strip()[:64],
            action=action,
            step=(step or "")[:32],
            comment=(comment or "").strip()[:2000],
        )
    )


async def submit_for_approval(
    db: AsyncSession,
    public_id: str,
    *,
    user: User,
    admin: bool,
) -> dict[str, object]:
    row = await _get_row(db, public_id)
    _assert_owner_or_admin(row, user, admin=admin)
    if (row.status or STATUS_PROCESSING) != STATUS_PROCESSING:
        raise AppError(ErrorCode.VALIDATION, "只有编制中的任务可以提交审批", status_code=422)
    biz_ok = bool((row.docx_file or "").strip()) and (tenders_output_dir() / row.docx_file).is_file()
    tech_name = (getattr(row, "tech_docx_file", None) or "").strip()
    tech_ok = bool(tech_name) and (tenders_output_dir() / tech_name).is_file()
    if not biz_ok and not tech_ok:
        raise AppError(ErrorCode.VALIDATION, "Word 文件缺失，无法提交审批", status_code=422)
    flow = await get_tender_flow(db, can_edit=False)
    steps = flow.get("steps") if isinstance(flow, dict) else None
    chain = review_steps(steps if isinstance(steps, list) else default_steps())
    if not chain:
        raise AppError(ErrorCode.VALIDATION, "请先配置至少一个审批环节", status_code=422)
    snapshot_steps = steps if isinstance(steps, list) else default_steps()
    row.status = STATUS_PENDING
    row.approval_snapshot = snapshot_from_steps(snapshot_steps)
    row.current_step = str(chain[0].get("key") or KEY_SUBMIT)
    row.submitted_at = _now()
    await _add_log(
        db,
        row,
        user=user,
        action=ACTION_SUBMIT,
        step=str(chain[0].get("name") or row.current_step)[:32],
        comment="",
    )
    await db.commit()
    await db.refresh(row)
    extras = await _list_log_extras(db, [int(row.id)])
    return _record_to_dict(row, extra=extras.get(int(row.id)))


async def decide_approval(
    db: AsyncSession,
    public_id: str,
    *,
    user: User,
    passed: bool,
    comment: str = "",
) -> dict[str, object]:
    row = await _get_row(db, public_id)
    if (row.status or "") != STATUS_PENDING:
        raise AppError(ErrorCode.VALIDATION, "当前任务不在待审批状态", status_code=422)
    steps = steps_from_snapshot(getattr(row, "approval_snapshot", None))
    if not steps:
        flow = await get_tender_flow(db, can_edit=False)
        raw_steps = flow.get("steps") if isinstance(flow, dict) else None
        steps = raw_steps if isinstance(raw_steps, list) else default_steps()
    reviews = review_steps(steps)
    current = find_step(steps, row.current_step) or (reviews[0] if reviews else None)
    if not user_can_act_on_step(user, current):
        raise AppError(ErrorCode.FORBIDDEN, "当前环节不由你审批", status_code=403)
    now = _now()
    if passed:
        new_status, new_step = next_step_on_pass(row.current_step, steps)
        row.status = new_status
        row.current_step = new_step
        if new_status == STATUS_APPROVED:
            row.decided_at = now
        log_step = display_step_name(steps, new_step)[:32]
        await _add_log(
            db,
            row,
            user=user,
            action=ACTION_PASS,
            step=log_step,
            comment=comment,
        )
    else:
        row.status = STATUS_PROCESSING
        row.current_step = KEY_SUBMIT
        row.decided_at = now
        await _add_log(
            db,
            row,
            user=user,
            action=ACTION_REJECT,
            step=KEY_SUBMIT,
            comment=comment or "驳回",
        )
    await db.commit()
    await db.refresh(row)
    extras = await _list_log_extras(db, [int(row.id)])
    return _record_to_dict(row, extra=extras.get(int(row.id)))


async def mark_result(
    db: AsyncSession,
    public_id: str,
    *,
    user: User,
    admin: bool,
    status: str,
) -> dict[str, object]:
    target = (status or "").strip()
    if target not in {STATUS_SUBMITTED, STATUS_WON, STATUS_LOST}:
        raise AppError(ErrorCode.VALIDATION, "结果只能是已提交 / 已中标 / 已失败", status_code=422)
    row = await _get_row(db, public_id)
    _assert_owner_or_admin(row, user, admin=admin)
    current = row.status or STATUS_PROCESSING
    if target == STATUS_SUBMITTED and current != STATUS_APPROVED:
        raise AppError(ErrorCode.VALIDATION, "审批通过后才能标记已提交", status_code=422)
    if target in {STATUS_WON, STATUS_LOST} and current not in {STATUS_APPROVED, STATUS_SUBMITTED}:
        raise AppError(ErrorCode.VALIDATION, "审批通过后才能标记中标结果", status_code=422)
    row.status = target
    row.current_step = KEY_DONE
    await _add_log(db, row, user=user, action=ACTION_MARK, step=row.current_step, comment=target)
    await db.commit()
    await db.refresh(row)
    if target in {STATUS_WON, STATUS_LOST}:
        try:
            from api.services.tenders.bid_marks import seed_record_marks

            await seed_record_marks(db, row)
        except Exception:
            logging.getLogger("api.tenders.records").exception("章节初标失败 record=%s", row.id)
    extras = await _list_log_extras(db, [int(row.id)])
    return _record_to_dict(row, extra=extras.get(int(row.id)))


async def record_ids_for_actor(
    db: AsyncSession,
    *,
    user_id: int,
    actions: tuple[str, ...],
) -> list[int]:
    rows = (
        await db.execute(
            select(TenderApprovalLog.record_id)
            .where(
                TenderApprovalLog.user_id == int(user_id),
                TenderApprovalLog.action.in_(actions),
            )
            .distinct()
        )
    ).all()
    return [int(r[0]) for r in rows]


def _qa_brief(row: TenderRecord) -> BidBrief:
    if not isinstance(row.brief_json, dict) or not row.brief_json:
        raise AppError(ErrorCode.VALIDATION, "该记录没有保存表单，无法自检", status_code=422)
    try:
        return BidBrief.model_validate(row.brief_json)
    except Exception as exc:
        raise AppError(ErrorCode.VALIDATION, f"记录表单无法解析：{exc}", status_code=422) from exc


def _record_qa_volume(brief: BidBrief, volume: str | None = None) -> str:
    """综合标始终按完整标检查，兼容仍发送 business 的旧前端。"""
    from api.services.tenders.qa import normalize_qa_volume

    generated = generate_volume(brief)
    if generated == "full" and not brief.generateBoth:
        return "full"
    want = (volume or "").strip().lower()
    if want == "full":
        raise AppError(ErrorCode.VALIDATION, "该记录不是综合标，请选择商务标或技术标", status_code=422)
    return normalize_qa_volume(want or generated)


def _qa_generated_file(
    row: TenderRecord,
    brief: BidBrief,
    volume: str | None = None,
) -> str:
    biz = (row.docx_file or "").strip()
    tech = (getattr(row, "tech_docx_file", None) or "").strip()
    resolved = _record_qa_volume(brief, volume)
    if resolved == "full":
        if biz:
            return biz
        raise AppError(ErrorCode.VALIDATION, "该记录没有完整投标文件 Word，无法自检", status_code=422)
    want = (volume or "").strip().lower()
    if want == "technical":
        if tech:
            return tech
        raise AppError(ErrorCode.VALIDATION, "该记录没有技术标 Word，无法自检", status_code=422)
    if want == "business":
        if biz:
            return biz
        raise AppError(ErrorCode.VALIDATION, "该记录没有商务标 Word，无法自检", status_code=422)
    vol = generate_volume(brief)
    if vol == "technical" and tech:
        return tech
    if biz:
        return biz
    if tech:
        return tech
    raise AppError(ErrorCode.VALIDATION, "该记录没有 Word 文件，无法自检", status_code=422)


async def inspect_record_qa(
    db: AsyncSession,
    public_id: str,
    *,
    volume: str | None = None,
) -> dict[str, object]:
    """对照邀请书对已生成 Word 做 AI 质检。"""
    from api.services.tenders.qa import inspect_bid

    row = await _get_row(db, public_id)
    brief = _qa_brief(row)
    target = _qa_generated_file(row, brief, volume)
    vol = _record_qa_volume(brief, volume)
    return await inspect_bid(
        db,
        public_id=row.public_id,
        brief=brief,
        docx_file=str(target),
        source="generated",
        volume=vol,
    )


async def inspect_record_qa_upload(
    db: AsyncSession,
    public_id: str,
    *,
    data: bytes,
    filename: str,
    volume: str | None = None,
) -> dict[str, object]:
    """对照邀请书对用户上传的终稿 Word 做复检。"""
    from api.services.tenders.qa import inspect_bid, normalize_qa_volume, save_qa_upload

    row = await _get_row(db, public_id)
    brief = _qa_brief(row)
    path = save_qa_upload(row.public_id, data)
    vol = _record_qa_volume(brief, volume)
    vol = normalize_qa_volume(vol)
    biz = (row.docx_file or "").strip()
    tech = (getattr(row, "tech_docx_file", None) or "").strip()
    label = (tech if vol == "technical" else biz) or path.name
    return await inspect_bid(
        db,
        public_id=row.public_id,
        brief=brief,
        docx_file=str(label),
        bid_path=path,
        source="upload",
        upload_name=filename,
        volume=vol,
    )


def get_saved_qa(
    public_id: str,
    *,
    volume: str | None = None,
    docx_file: str | None = None,
) -> dict[str, object]:
    from api.services.tenders.qa import load_qa_report, normalize_qa_volume

    vol = normalize_qa_volume(volume) if volume else None
    report = load_qa_report(public_id, volume=vol)
    if not report:
        return {"report": None}
    source = str(report.get("source") or "generated")
    if source == "upload":
        try:
            from api.services.tenders.qa import qa_upload_path

            path = qa_upload_path(public_id)
            stat = path.stat()
            current = hashlib.sha256(f"{stat.st_mtime_ns}:{stat.st_size}".encode("ascii")).hexdigest()[:24]
            stale = bool(report.get("inputFingerprint") and current != report.get("inputFingerprint"))
            if not report.get("inputFingerprint"):
                checked = int(report.get("checkedAt") or 0) / 1000
                stale = stat.st_mtime > checked if checked else False
        except (OSError, AppError):
            stale = True
        report["stale"] = stale
    else:
        stale = bool(
            docx_file and report.get("docxFile") and str(report.get("docxFile")) != str(docx_file)
        )
        if not stale and docx_file:
            try:
                from api.services.tenders.generate import resolve_output_file

                path = resolve_output_file(docx_file)
                stat = path.stat()
                if report.get("inputFingerprint"):
                    current = hashlib.sha256(f"{stat.st_mtime_ns}:{stat.st_size}".encode("ascii")).hexdigest()[:24]
                    stale = current != report.get("inputFingerprint")
                else:
                    checked = int(report.get("checkedAt") or 0) / 1000
                    stale = stat.st_mtime > checked if checked else False
            except (OSError, AppError):
                stale = True
        report["stale"] = stale
    return {"report": report}


async def get_record_qa(
    db: AsyncSession, public_id: str, *, volume: str | None = None
) -> dict[str, object]:
    from api.services.tenders.qa import load_qa_report, normalize_qa_volume

    row = await _get_row(db, public_id)
    want = (volume or "").strip().lower()
    vol = normalize_qa_volume(want) if want else None
    brief = _qa_brief(row)
    if generate_volume(brief) == "full" and not brief.generateBoth:
        vol = "full"
    elif vol == "full":
        _record_qa_volume(brief, vol)
    if not vol:
        latest = load_qa_report(row.public_id)
        vol = normalize_qa_volume(str((latest or {}).get("volume") or ""))
    tech = (getattr(row, "tech_docx_file", None) or "").strip()
    biz = (row.docx_file or "").strip()
    docx = tech if vol == "technical" else biz
    return get_saved_qa(row.public_id, volume=vol, docx_file=docx)
