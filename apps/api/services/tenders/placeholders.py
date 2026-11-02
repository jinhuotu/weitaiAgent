"""投标 Word 中缺失扫描件的小标题 + 方框占位。程序排版，不经 LLM。"""

from __future__ import annotations

import io
from copy import deepcopy
import hashlib
import logging
import re
import unicodedata
from math import ceil
from pathlib import Path

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph

from api.services.tenders.schema import PlaceholderItem

logger = logging.getLogger("api.tenders")

_SONG = "宋体"
_CN_ORD = "一二三四五六七八九十"
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

# 证件类写入 Word；合同/财税等 PDF 仍占位，避免生成卡住。
_EMBED_SCAN_KEYS = frozenset({"id_legal", "id_agent"})
_EMBED_PDF_KEYS = frozenset({"id_legal", "id_agent", "credit"})
_SLOT_PDF_MAX_PAGES = 1
_CREDIT_PDF_MAX_PAGES = 4
_SLOT_MAX_FILES = 2
_CREDIT_MAX_FILES = 6
_PLACEHOLDER_MAX_IMAGES = 8
_RENDER_SCALE = 0.5
_JPEG_QUALITY = 45
_IMAGE_MAX_PX = 800
# 信用中国等文字页按约 220dpi 出 PNG，不再走 JPEG 45/800px
_CREDIT_DPI = 220
_CREDIT_MAX_PX = 2800
_CREDIT_MAX_H_CM = 12.0
# 身份证正反面：按实物尺寸上限并排，窄版心等比缩小
_ID_PAIR_MAX_IMAGES = 2
_ID_STRIP_WIDTH_CM = 17.4
_ID_SINGLE_WIDTH_CM = 8.6
_ID_STRIP_MAX_H_CM = 5.4
_ID_PREVIEW_DPI = 96
_ID_ASPECT_MIN = 1.22
_ID_ASPECT_MAX = 1.98
_HEADING_CM = 1.1
_BOX_CM = 5.4
_SLOT_GAP_STYLE = "TenderAttachmentGap"
_SLOT_CLEARANCE_STYLE = "TenderTableClearance"
_SLOT_GAP_CM = 0.5
_IMAGE_MAX_H_CM = 12.0
_TWIPS_PER_CM = 567
_EMU_PER_CM = 360000.0

DEFAULT_SLOTS: tuple[PlaceholderItem, ...] = (
    PlaceholderItem(
        key="id_legal",
        title="法定代表人身份证正反面",
        hint="可放多位法定代表人。每人上传正面和反面，正面识别姓名和证号；做标书时再选本标用谁。",
    ),
    PlaceholderItem(
        key="id_agent",
        title="授权代理人身份证及近半年社保缴纳证明",
        hint="法人亲自投标可划掉本项。须为本人证件。",
    ),
    PlaceholderItem(
        key="license",
        title="营业执照",
        hint="企业法人营业执照副本扫描件，不要用身份证或其他证件顶替。",
    ),
    PlaceholderItem(
        key="bank_permit",
        title="开户许可证",
        hint="基本账户开户许可证扫描件。",
    ),
    PlaceholderItem(
        key="iso",
        title="ISO/质量管理体系认证证书",
        hint="按邀请书资质标题一对一提供，不要拿其他证书顶。",
    ),
    PlaceholderItem(
        key="perf",
        title="类似项目合同及发票",
        hint="签约主体须与本次投标人一致。上传后识别项目、签约主体和合同金额，按本次招标条款筛选。",
    ),
    PlaceholderItem(
        key="finance",
        title="近三年财务审计报告 / 完税证明",
        hint="按招标文件资格审查条款提供。纳税完税放本项，不要和社保证明混放。",
    ),
    PlaceholderItem(
        key="social",
        title="社保缴纳证明",
        hint="近半年社保缴纳证明。与纳税完税、身份证分开上传。",
    ),
    PlaceholderItem(
        key="credit",
        title="信用中国查询页及国家企业信用信息公示（股东）",
        hint="截图须含查询日期。",
    ),
    PlaceholderItem(
        key="product",
        title="所投产品检测报告 / 认证 / 参数证明",
        hint="产品型号、参数和认证应与招标要求一致。",
    ),
    PlaceholderItem(
        key="bond",
        title="投标保证金缴存回单",
        hint="按邀请书载明的金额与账户缴纳后替换。",
    ),
    PlaceholderItem(
        key="seal",
        title="投标文件签章页",
        hint="逐页盖投标人公章；法定代表人签字或盖章。",
    ),
)

CRITICAL_SLOT_KEYS = frozenset(item.key for item in DEFAULT_SLOTS if item.key != 'product')

# Evidence comes from this invitation, never from the library's own descriptions.
_MATERIAL_SECTION_MARKS = (
    ('id_legal', ('法定代表人身份证', '法人身份证', '法人代表身份证')),
    ('id_agent', ('代理人身份证', '被授权人身份证', '委托人身份证')),
    ('license', ('营业执照', '独立承担民事责任', '独立法人资格')),
    ('bank_permit', ('开户许可', '基本账户', '基本存款账户', '基本户')),
    ('finance', (
        '财务状况', '财务审计', '审计报告', '财务报告', '财务报表', '财务会计制度',
        '完税证明', '纳税证明', '纳税凭证', '依法缴纳税收', '税收及社会保障',
    )),
    ('social', ('社保缴纳', '社保缴费', '社保证明', '社会保障资金')),
    ('credit', ('信用中国', '信用记录', '信用查询', '失信查询', '企业信用信息公示')),
    ('perf', ('业绩', '类似项目', '合同及发票', '履约证明')),
    ('iso', ('资质证书', '资质证明', '资质材料', '质量体系', '体系认证', 'iso')),
    ('bond', ('投标保证金', '保证金回单', '保证金缴存', '保证金缴纳')),
    ('seal', ('签章页', '盖章页', '印鉴备案', '印鉴及签字')),
)


def _material_keys(text: str) -> list[str]:
    compact = re.sub(r'\s+', '', text or '').lower()
    keys = []
    for key, marks in _MATERIAL_SECTION_MARKS:
        for mark in marks:
            start = compact.find(mark)
            if start < 0:
                continue
            before, after = compact[max(0, start - 12):start], compact[start + len(mark):]
            if (re.search(r'(?:无需|无须|免交|免缴|不收取|不要求).{0,4}$', before)
                    or re.match(r'[：:]?(?:不收取|免交|免缴|无需提供|无须提供)', after)):
                continue
            keys.append(key)
            break
    keys.extend(_paste_box_keys(compact))
    if compact in {'资质', '资质要求'} and 'iso' not in keys:
        keys.append('iso')
    return keys


def _with_format_paste(invitation_id: str, source: str) -> str:
    invite = (invitation_id or "").strip()
    if not invite:
        return source or ""
    from api.services.tenders.pdf_format import format_paste_source

    extra = format_paste_source(invite)
    if not extra:
        return source or ""
    return ((source or "").rstrip() + "\n" + extra).strip()


def _paste_box_keys(compact: str) -> list[str]:
    if "此处粘贴" not in compact and "在此粘贴" not in compact:
        return []
    keys = []
    if "营业执照" in compact:
        keys.append("license")
    if any(word in compact for word in ("纳税", "完税", "税收", "财务", "审计")):
        keys.append("finance")
    if "社保" in compact or "社会保障" in compact:
        keys.append("social")
    if "身份证" in compact:
        keys.append("id_agent" if ("委托" in compact or "代理" in compact) else "id_legal")
    return keys


def _bid_material_keys(outline_items: list | None, requirements: list | None,
                       source_text: str) -> list[str]:
    keys = []
    for item in outline_items or []:
        if getattr(item, 'skipped', False):
            continue
        text = '\n'.join(str(getattr(item, attr, '') or '') for attr in
                         ('title', 'rawTitle', 'body', 'inviteExcerpt'))
        keys.extend(_material_keys(text))
        if getattr(item, 'kind', '') == 'performance':
            keys.append('perf')
    for req in requirements or []:
        key = str(getattr(req, 'slotKey', '') or '').strip()
        if key:
            keys.append(key)
        if getattr(req, 'category', '') not in {'qualification', 'material'} and not key:
            continue
        keys.extend(_material_keys('\n'.join(str(getattr(req, attr, '') or '') for attr in
                                              ('text', 'targetTitle', 'attachToSection'))))
    for line in (source_text or '').splitlines():
        keys.extend(_material_keys(line))
    return normalize_slot_keys(keys)

TECH_DRAWING_KEY = "tech_drawings"
TECH_DRAWING_SLOT = PlaceholderItem(
    key=TECH_DRAWING_KEY,
    title="本项目图纸",
    hint="本标平面图、系统图或施工图。换一份邀请书需重新上传，不要用其他项目图纸。",
)

_PERF_NOTE = (
    "【待补】请粘贴本次投标人作为签约主体的合同及招标要求的证明材料。"
    "按本标邀请书业绩门槛筛选，禁止使用其他公司业绩。"
)


def normalize_slot_keys(keys: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in keys or []:
        key = str(raw or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def this_bid_keys(
    *,
    extra: list[PlaceholderItem] | None = None,
    catalog: list[PlaceholderItem] | None = None,
    required_keys: list[str] | None = None,
    include_keys: list[str] | None = None,
    has_agent: bool = False,
    outline_items: list | None = None,
    requirements: list | None = None,
    source_text: str = '',
    invitation_id: str = '',
    material_demands: list | None = None,
) -> tuple[list[str], list[str]]:
    """本标必填 key、写入 Word 的 key。不把整份资料库都塞进文件。"""
    def canonical_keys(required, include):
        # Slot collection and its callers must refer to the same keys; keeping
        # an old alias here would falsely report a missing canonical file.
        from api.services.tenders.material_identity import material_key_aliases

        aliases = material_key_aliases([*DEFAULT_SLOTS, *(catalog or []), *(extra or [])])
        dismissed = {aliases.get(key, key) for key in getattr(catalog, 'dismissed_keys', ())}
        real = {aliases.get(key, key) for key in getattr(catalog, 'real_keys', ())}

        def normalize(keys):
            return normalize_slot_keys([aliases.get(key, key) for key in keys
                if aliases.get(key, key) not in dismissed or aliases.get(key, key) in real])

        return normalize(required), normalize(include)

    if material_demands is not None:
        required=normalize_slot_keys([d.slotKey for d in material_demands
            if not (d.condition=='agent' and not has_agent)])
        # Recognition owns obligations. Catalog availability and legacy word
        # matches must never manufacture another required item after review.
        return canonical_keys(required, normalize_slot_keys([*required,*(include_keys or [])]))
    source_text = _with_format_paste(invitation_id, source_text)
    extras = extra or []
    known = {item.key for item in (catalog if catalog is not None else DEFAULT_SLOTS) if item.key}
    known.update(item.key for item in DEFAULT_SLOTS)
    known.update(item.key for item in extras if item.key)

    required = normalize_slot_keys(required_keys)
    if not required:
        required = normalize_slot_keys([item.key for item in extras])
    required = normalize_slot_keys([
        *required, *_bid_material_keys(outline_items, requirements, source_text),
    ])
    if "id_legal" in known and "id_legal" not in required:
        required = ["id_legal", *required]
    demanded_agent = any(s.key=='id_agent' and s.demandIds for s in extras)
    if has_agent:
        if "id_agent" in known and "id_agent" not in required:
            required.append("id_agent")
    elif not demanded_agent:
        required = [key for key in required if key != "id_agent"]
    # Known catalog keys are a mapping aid, not an admission list. Preserve
    # explicit upload-safe requirement keys even before a library item exists.
    safe_key = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$')
    required = [key for key in required if key in known or safe_key.fullmatch(key)]

    include = normalize_slot_keys(include_keys)
    if not include:
        include = list(required)
    else:
        for key in required:
            if key not in include:
                include.append(key)
    include = [key for key in include if key in known or safe_key.fullmatch(key)]
    for item in catalog if catalog is not None else DEFAULT_SLOTS:
        key = (item.key or "").strip()
        if not key or key not in known or key in include:
            continue
        if _always_in_bid_scan(item):
            include.append(key)
    if not has_agent and not demanded_agent:
        include = [key for key in include if key != "id_agent"]
    return canonical_keys(required, include)


_ALWAYS_SCAN_MARKS = (
    ("license", ("营业执照",)),
    ("bank_permit", ("开户许可", "开户许可证", "基本账户", "基本户")),
    ("iso", ("ISO", "iso", "质量体系", "体系认证", "资质证书")),
)


def _always_in_bid_scan(item: PlaceholderItem) -> bool:
    key = (item.key or "").strip()
    if key in {"license", "bank_permit", "iso"}:
        return True
    blob = f"{item.title or ''}{item.hint or ''}"
    return any(any(m in blob for m in marks) for _, marks in _ALWAYS_SCAN_MARKS)


def collect_slots(
    extra: list[PlaceholderItem] | None = None,
    *,
    catalog: list[PlaceholderItem] | None = None,
    include_keys: list[str] | None = None,
    outline_items: list | None = None,
) -> list[PlaceholderItem]:
    """资料库清单 + 邀请书多出来的资料项（按 key 去重，后者可改说明）。

    include_keys 为 None 时返回合并后的全部项（资料库列表）；传入列表则只保留本标要写入 Word 的项。
    """
    from api.services.tenders.material_identity import material_key_aliases

    dismissed = set(getattr(catalog, 'dismissed_keys', ()))
    real_keys = set(getattr(catalog, 'real_keys', ()))
    base_items = list(catalog) if catalog is not None else list(DEFAULT_SLOTS)
    present = {item.key for item in base_items}
    base_items.extend(item for item in DEFAULT_SLOTS if item.key not in present)
    by_key: dict[str, PlaceholderItem] = {
        item.key: item.model_copy() for item in base_items if item.key
    }
    for item in extra or []:
        title = (item.title or "").strip()
        if not title:
            continue
        key = (item.key or "").strip() or f"extra_{len(by_key) + 1}"
        hint = (item.hint or "").strip()
        if key in by_key:
            current = by_key[key]
            by_key[key] = PlaceholderItem(
                key=key,
                title=item.title if item.demandIds else current.title,
                hint=hint or current.hint,
                attachToSection=(item.attachToSection or current.attachToSection or ""),
                demandIds=list(item.demandIds or current.demandIds),
            )
        else:
            by_key[key] = PlaceholderItem(
                key=key,
                title=title,
                hint=hint or "请按招标文件要求补附",
                attachToSection=item.attachToSection or "",
                demandIds=list(item.demandIds),
            )
    aliases = material_key_aliases(list(by_key.values()))
    dismissed = {aliases.get(key, key) for key in dismissed}
    real_keys = {aliases.get(key, key) for key in real_keys}
    merged: dict[str, PlaceholderItem] = {}
    for key, item in by_key.items():
        canonical = aliases[key]
        if canonical in dismissed and canonical not in real_keys:
            continue
        current = merged.get(canonical)
        if current is None:
            merged[canonical] = item.model_copy(update={'key': canonical})
        else:
            current.demandIds = list(dict.fromkeys([*current.demandIds, *item.demandIds]))
            current.attachToSection = current.attachToSection or item.attachToSection
            current.hint = current.hint or item.hint
    by_key = merged
    # Supply attachment routing metadata to the existing placement functions.
    # A broad qualification heading can own an explicit business-license request.
    for slot in by_key.values():
        if slot.attachToSection:
            continue
        candidates = [item for item in outline_items or [] if not getattr(item, 'skipped', False)]
        # Prefer a dedicated chapter over an incidental mention in a letter.
        for attrs in (('title', 'rawTitle'), ('body', 'inviteExcerpt')):
            hit = next((item for item in candidates
                        if (attrs[0] == 'title' or getattr(item, 'kind', '')
                            not in {'letter', 'legal_id', 'auth', 'quote'})
                        and slot.key in _material_keys('\n'.join(
                            str(getattr(item, attr, '') or '') for attr in attrs))), None)
            if hit is not None:
                slot.attachToSection = getattr(hit, 'rawTitle', '') or hit.title
                break
    if include_keys is None:
        return list(by_key.values())
    ordered: list[PlaceholderItem] = []
    seen: set[str] = set()
    for key in normalize_slot_keys(include_keys):
        key = aliases.get(key, key)
        if key in dismissed and key not in real_keys:
            continue
        item = by_key.get(key)
        if item is None and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}',key):
            item = PlaceholderItem(key=key,title=key,hint='本次标书明确要求的资料，名称待核对，请补充后上传')
        if item is None or key in seen:
            continue
        seen.add(key)
        ordered.append(item)
    return ordered


def _is_credit_slot(slot: PlaceholderItem) -> bool:
    if (slot.key or "").strip() == "credit":
        return True
    n = f"{slot.title or ''}{slot.hint or ''}"
    if "信用" not in n:
        return False
    return any(k in n for k in ("截图", "查询", "公示", "信用中国", "失信"))


def _is_embed_pdf_slot(slot: PlaceholderItem) -> bool:
    key = (slot.key or "").strip()
    if key in _EMBED_PDF_KEYS or "身份证" in (slot.title or ""):
        return True
    return _is_credit_slot(slot)


def _merge_credit_slots(
    slots: list[PlaceholderItem],
    files_by_key: dict[str, list[Path]] | None,
) -> tuple[list[PlaceholderItem], dict[str, list[Path]]]:
    files_by_key = {k: list(v) for k, v in (files_by_key or {}).items()}
    credit = [s for s in slots if _is_credit_slot(s)]
    if len(credit) <= 1:
        return slots, files_by_key
    keep = next((s for s in credit if (s.key or "").strip() == "credit"), credit[0])
    merged: list[Path] = []
    seen: set[str] = set()
    for s in credit:
        for p in files_by_key.get(s.key) or []:
            mark = str(p)
            if mark in seen:
                continue
            seen.add(mark)
            merged.append(p)
    files_by_key[keep.key] = merged
    out: list[PlaceholderItem] = []
    used = False
    for s in slots:
        if not _is_credit_slot(s):
            out.append(s)
            continue
        if used:
            continue
        out.append(keep)
        used = True
    return out, files_by_key


def id_slot(key: str) -> PlaceholderItem:
    for item in DEFAULT_SLOTS:
        if item.key == key:
            return item
    title = "法定代表人身份证正反面" if key == "id_legal" else "授权代理人身份证正反面"
    return PlaceholderItem(key=key, title=title)


def inline_id_scans(
    doc: Document,
    files: list[Path] | None,
    *,
    empty_slot: PlaceholderItem | None = None,
    before: Paragraph | None = None,
) -> int:
    """Compatibility adapter for identity material at its owned block boundary."""
    slot=empty_slot or PlaceholderItem(key='id_legal',title='法定代表人身份证')
    from api.services.tenders.pdf_format import toc_block_ids
    anchor,_=_locate_note(doc,slot,set(),toc_block_ids(doc))
    if anchor is None:
        raise ValueError('No owned identity attachment site for '+slot.title)
    placed=_drop_after(doc,anchor,slot,list(files or []),complete=True,at_marker=True)
    return min(2,len(files or [])) if placed else 0


def append_placeholder_section(
    doc: Document,
    slots: list[PlaceholderItem],
    attachments: dict[str, list[Path]] | None = None,
    *,
    before: Paragraph | None = None,
    heading: bool = True,
) -> tuple[int, int, list[str]]:
    """Legacy API delegates to owned-section placement; never append a chapter."""
    notes=place_unmatched_slots(doc,slots=slots,media=attachments,
                                required_keys=[slot.key for slot in slots])
    import hashlib
    marks={b.get(qn('w:name'),'') for b in doc.element.body.iter(qn('w:bookmarkStart'))}
    prefixes=['result_'+hashlib.sha256(slot.key.encode()).hexdigest()[:20] for slot in slots]
    pictures=sum(prefix+'_img' in marks for prefix in prefixes)
    boxes=sum(prefix+'_box' in marks for prefix in prefixes)
    return pictures,boxes,notes


def _last_body_child(doc: Document):
    body = doc.element.body
    last = None
    for child in list(body):
        if child.tag == qn("w:sectPr"):
            break
        last = child
    return last


def _relocate_appended_before(doc: Document, *, before: Paragraph, last_before) -> None:
    body = doc.element.body
    to_move = []
    passed = last_before is None
    for child in list(body):
        if child.tag == qn("w:sectPr"):
            continue
        if not passed:
            if child is last_before:
                passed = True
            continue
        to_move.append(child)
    for el in to_move:
        before._element.addprevious(el)


_LEVEL1 = re.compile(
    r"^(?:第[一二三四五六七八九十0-9]+[章节]|[一二三四五六七八九十]{1,3}、|\d+[.、])"
)


def _compact_para(text: str) -> str:
    return "".join((text or "").split())


def _level1_core(text: str) -> str:
    n = _compact_para(text)
    if not n or len(n) > 36 or "如有" in n or "。" in n:
        return ""
    if n.startswith(("（", "(")):
        return ""
    if _LEVEL1.match(n) is None:
        return ""
    n = re.sub(r"^第[一二三四五六七八九十0-9]+[章节]", "", n)
    n = re.sub(r"^[一二三四五六七八九十]{1,3}、", "", n)
    n = re.sub(r"^\d+[.、]", "", n)
    return n


def _para_text(el) -> str:
    return "".join(node.text or "" for node in el.iter(qn("w:t")))


_NO_PASTE = {
    "投标函",
    "投标函附录",
    "目录",
    "目次",
    "法定代表人身份证明",
    "法定代表人身份证明书",
    "授权委托书",
    "中小企业声明函",
    "残疾人福利性单位声明函",
}
_CHAPTER_MARKS = (
    "投标保证金",
    "已标价工程量清单",
    "已标价的工程量清单",
    "工程量清单",
    "已标价",
    "营业执照",
    "开户许可",
    "基本账户",
    "基本户",
    "信用中国",
    "财务审计",
    "财务报表",
    "类似项目",
    "资质证明",
    "资质证书",
    "安全生产许可",
    "检测报告",
    "授权委托",
)
_KEY_CHAPTER = {
    "bond": "投标保证金",
    "license": "营业执照",
    "bank_permit": "开户许可",
    "perf": "类似项目",
    "finance": "财务审计",
    "credit": "信用中国",
    "iso": "资质证书",
    "quote": "工程量清单",
}


def _heading_core(text: str) -> str:
    n = _compact_para(text)
    if not n or len(n) > 36 or "如有" in n or "。" in n or n.startswith(("（", "(")):
        return ""
    core = _level1_core(text)
    if core:
        return core
    # 组成清单里的「身份证明或授权委托书」不是专章
    if any(x in n for x in ("或", "；", ";", "，", "须", "提供")):
        return ""
    if len(n) > 18:
        return ""
    for mark in _CHAPTER_MARKS:
        if n == mark or (n.endswith(mark) and len(n) - len(mark) <= 2):
            return n
    return ""


def _letter_block(doc: Document) -> list:
    """投标函标题到下一章之前。这段正文不能插附件。"""
    els = []
    inside = False
    for para in doc.paragraphs:
        n = _compact_para(para.text or "")
        core = _level1_core(_section_label(para._p))
        if core:
            inside = core == "投标函" or core.startswith("投标函")
            if inside:
                els.append(para._p)
            continue
        if n.startswith("投标函") or n.startswith("（一）投标函") or n.startswith("(一)投标函"):
            inside = True
            els.append(para._p)
            continue
        if inside and _is_other_title(para.text or ""):
            inside = False
            continue
        if inside and _heading_core(para.text or "") and not _paste_ban(para.text or ""):
            inside = False
            continue
        if inside:
            els.append(para._p)
    return els


def _chapter_last(heading_el):
    head = _para_text(heading_el)
    stop_sub = _is_other_title(head) or bool(_SUB_HEAD.match(_compact_para(head)))
    last = heading_el
    el = heading_el.getnext()
    while el is not None and el.tag != qn("w:sectPr"):
        if el.tag == qn("w:p"):
            text = _para_text(el)
            if _sec_head(_section_label(el)) or _paste_ban(text):
                break
            if stop_sub and _OTHER_PREFIX.match(_compact_para(text)):
                break
        if el.tag in {qn("w:p"), qn("w:tbl")}:
            last = el
        el = el.getnext()
    return last


def _move_after(doc: Document, last_el, marker) -> None:
    body = doc.element.body
    moved = []
    seen = marker is None
    for child in list(body):
        if not seen:
            if child is marker:
                seen = True
            continue
        if child.tag == qn("w:sectPr"):
            continue
        moved.append(child)
    anchor = _body_block(last_el)
    clearance = _floating_table_cm(anchor)
    if clearance:
        # wrapNone 的浮动原表不占正文高度；只给新增附件预留它的占位，原表 XML 不动。
        spacer = doc.add_paragraph()
        if _SLOT_CLEARANCE_STYLE not in doc.styles:
            doc.styles.add_style(_SLOT_CLEARANCE_STYLE, WD_STYLE_TYPE.PARAGRAPH)
        spacer.style = _SLOT_CLEARANCE_STYLE
        pf = spacer.paragraph_format
        pf.space_before = pf.space_after = Pt(0)
        pf.keep_with_next = False
        pf.keep_together = True
        pf.line_spacing = Cm(min(clearance, _page_body_cm(doc) - 0.5))
        spacer.add_run().font.size = Pt(1)
        anchor.addnext(spacer._p)
        anchor = spacer._p
    for child in moved:
        parent = child.getparent()
        if parent is not None:
            parent.remove(child)
        anchor.addnext(child)
        anchor = child
    _merge_slot_gaps(doc)


def _body_block(anchor):
    """单元格、表格行或文本框内的锚点必须提升到正文直属块。"""
    while anchor.getparent() is not None and anchor.getparent().tag != qn("w:body"):
        anchor = anchor.getparent()
    return anchor


def _floating_table_cm(block) -> float:
    """浮动范本表的占用高度；Choice/Fallback 取最大值，不能重复累加。"""
    heights = []
    for anchor in block.iter(qn("wp:anchor")):
        if next(anchor.iter(qn("w:tbl")), None) is None or anchor.find(qn("wp:wrapNone")) is None:
            continue
        extent = anchor.find(qn("wp:extent"))
        position = anchor.find(qn("wp:positionV"))
        offset = position.find(qn("wp:posOffset")) if position is not None else None
        if extent is not None:
            heights.append((int(extent.get("cy", "0")) + max(0, int(offset.text or "0") if offset is not None else 0)) / _EMU_PER_CM)
    for shape in block.iter("{urn:schemas-microsoft-com:vml}shape"):
        if next(shape.iter(qn("w:tbl")), None) is None:
            continue
        style = shape.get("style", "")
        if "position:absolute" not in style.replace(" ", ""):
            continue
        values = {}
        for key, value, unit in re.findall(r"(?:^|;)\s*(height|margin-top)\s*:\s*(-?[\d.]+)(pt|cm|mm|in)", style):
            values[key] = float(value) * {"pt": 2.54 / 72, "cm": 1, "mm": 0.1, "in": 2.54}[unit]
        heights.append(values.get("height", 0) + max(0, values.get("margin-top", 0)))
    if block.tag == qn("w:tbl"):
        positioning = block.find("w:tblPr/w:tblpPr", block.nsmap)
        if positioning is not None:
            heights.append(_tbl_cm(block) + max(0, int(positioning.get(qn("w:tblpY"), "0"))) / _TWIPS_PER_CM)
    return max(heights, default=0.0)


def _slot_before_boundary(anchor):
    """Keep a source's empty ending section/page boundary after new material."""
    anchor = _body_block(anchor)
    # _span_last/_chapter_last 会包含末尾空白分节段；在它后面插入等于另起一页。
    while anchor.tag == qn("w:p") and not _para_text(anchor).strip():
        if any(next(anchor.iter(qn("w:" + name)), None) is not None for name in ("drawing", "pict", "object", "tbl")):
            break
        ppr = anchor.find(qn("w:pPr"))
        page_before = ppr.find(qn("w:pageBreakBefore")) if ppr is not None else None
        boundary = (
            next(anchor.iter(qn("w:sectPr")), None) is not None
            or any(br.get(qn("w:type")) == "page" for br in anchor.iter(qn("w:br")))
            or (page_before is not None and page_before.get(qn("w:val"), "true") not in {"0", "false", "off"})
        )
        previous = anchor.getprevious()
        if not boundary or previous is None:
            break
        anchor = previous
    return anchor


def _slot_anchor(anchor):
    """附件放在本节分页符前；说明后紧接的原范本表格必须先保留。"""
    anchor = _slot_before_boundary(anchor)
    # Direct placement callers use the same semantic sentence-group boundary as
    # the section planner, including blank paragraphs before a following table.
    from api.services.tenders.section_materials import _sentence_boundary
    group = [anchor]
    cursor = anchor.getnext()
    while cursor is not None and cursor.tag != qn('w:sectPr'):
        if cursor.tag == qn('w:p') and _sec_head(_section_label(cursor)):
            break
        group.append(cursor)
        cursor = cursor.getnext()
    anchor = _sentence_boundary(anchor, group)
    # 不能把资料插在说明与原表之间；连续原表也作为同一个范本块保留。
    cursor = anchor.getnext()
    while cursor is not None and next(cursor.iter(qn("w:tbl")), None) is not None:
        text = _para_text(cursor)
        if "此处粘贴" in text or _INPLACE in text:
            break
        if _sec_head(_section_label(cursor)):
            break
        anchor = cursor
        cursor = cursor.getnext()
    return anchor


def _drop_after(doc: Document, anchor_el, slot: PlaceholderItem, files: list[Path], *, wording: str = "", complete: bool = False, at_marker: bool = False) -> bool:
    """The single block writer: never insert inside a paragraph or a table cell."""
    import hashlib
    from api.services.tenders.material_layout import constrain_blocks
    anchor_el = _body_block(anchor_el)
    if not at_marker:
        anchor_el = _slot_anchor(anchor_el)
    else:
        # A planned block endpoint still needs to remain before its chapter's
        # empty ending section break; this does not recompute ownership.
        anchor_el = _slot_before_boundary(anchor_el)
    body = doc.element.body
    if anchor_el is None or anchor_el.getparent() is not body:
        raise ValueError('Material anchor must be a body paragraph/table boundary')
    token = 'result_' + hashlib.sha256(slot.key.encode()).hexdigest()[:20]
    marks = [b.get(qn('w:name'),'') for b in body.iter(qn('w:bookmarkStart'))]
    next_bookmark_id=max((int(mark.get(qn('w:id'),'0')) for mark in body.iter(qn('w:bookmarkStart'))),default=0)+1
    if token+'_img' in marks or token+'_box' in marks:
        return token+'_img' in marks
    # A chapter-end locator may point to its last empty paragraph. Recover the
    # whole local blank run, without crossing text, tables or section breaks.
    while _slot_blank(doc, anchor_el) and anchor_el.getprevious() is not None:
        previous = anchor_el.getprevious()
        if previous.tag not in {qn('w:p'), qn('w:tbl')}:
            break
        anchor_el = previous
    blanks = []
    cursor = anchor_el.getnext()
    while cursor is not None and _slot_blank(doc, cursor):
        blanks.append(cursor)
        cursor = cursor.getnext()
    nodes = list(body)
    replace_empty_frame=(anchor_el.tag==qn('w:p') and not _para_text(anchor_el).strip()
        and anchor_el.find('w:pPr/w:pBdr',anchor_el.nsmap) is not None
        and next(anchor_el.iter(qn('w:drawing')),None) is None
        and next(anchor_el.iter(qn('w:sectPr')),None) is None)
    tail = [node for node in nodes[nodes.index(anchor_el)+1:] if node.tag != qn('w:sectPr')]
    for node in tail:body.remove(node)
    try:
        identity = slot.key in {'id_legal','id_agent'} or '身份证' in (slot.title or '')
        placed = bool(files and _insert_slot_media(doc,files if complete else files[:2],
            max_pages=2**31-1 if complete else _SLOT_PDF_MAX_PAGES,compact_pair=identity))
        if not placed:
            name=(slot.title or '待补资料').removeprefix('对应').strip()
            _draw_box(doc,slot,wording=wording or ('对应'+name+'；所需材料：'+name))
        _move_after(doc,anchor_el,anchor_el)
        added=[node for node in list(body)[list(body).index(anchor_el)+1:] if node.tag != qn('w:sectPr')]
        for node in added:
            for br in list(node.iter(qn('w:br'))):
                if br.get(qn('w:type'))=='page':
                    br.getparent().remove(br)
            for props in node.iter(qn('w:pPr')):
                for flag in list(props.findall(qn('w:pageBreakBefore'))):
                    props.remove(flag)
        constrain_blocks(doc,anchor_el,added)
        host=next((p for node in added for p in node.iter(qn('w:p'))
            if list(p.iter(qn('w:drawing'))) or p.find('w:pPr/w:pBdr',p.nsmap) is not None),None)
        if host is None:
            raise ValueError('Material output has neither an image nor a labelled placeholder')
        if not placed and not _para_text(host).strip():
            raise ValueError('Placeholder label is empty')
        ident=next_bookmark_id
        start=OxmlElement('w:bookmarkStart');start.set(qn('w:id'),str(ident));start.set(qn('w:name'),token+('_img' if placed else '_box'))
        end=OxmlElement('w:bookmarkEnd');end.set(qn('w:id'),str(ident))
        host.append(start);host.append(end)
        # Render first, so capacity checks use the actual image/frame blocks.
        # Ordinary generated half-centimetre spacers must not consume a source
        # blank region that was already reserved for attachments.
        material = [node for node in added if not _is_slot_gap(node)]
        if blanks and _reuse_slot_blanks(doc, blanks, material, previous=anchor_el, following=cursor):
            for node in added:
                body.remove(node)
        else:
            # Insufficient blanks retain their source order before the material.
            # Count this prefix, never the following chapter or the material
            # itself, when estimating the current page's remaining capacity.
            for node in added:
                body.remove(node)
            for blank in blanks:
                body.insert(len(body) - (1 if body.find(qn('w:sectPr')) is not None else 0), blank)
                tail.remove(blank)
            _break_before_slot(doc, sum(_slot_height_cm(doc, node) for node in added))
            for node in added:
                body.insert(len(body) - (1 if body.find(qn('w:sectPr')) is not None else 0), node)
        if replace_empty_frame:
            body.remove(anchor_el)
    finally:
        sect=body.find(qn('w:sectPr'))
        for node in tail:
            if sect is not None:sect.addprevious(node)
            else:body.append(node)
    return placed


def _slot_blank(doc, node):
    """Only reuse genuine body whitespace, never source anchors or boundaries."""
    if node.tag != qn('w:p') or _para_text(node).strip() or _is_slot_gap(node):
        return False
    protected = ('drawing', 'pict', 'object', 'tbl', 'sectPr', 'br', 'lastRenderedPageBreak',
                 'bookmarkStart', 'bookmarkEnd', 'fldChar', 'instrText', 'sdt', 'pBdr', 'numPr', 'framePr')
    if any(next(node.iter(qn('w:' + name)), None) is not None for name in protected):
        return False
    # Textless is not necessarily empty: symbols, footnote references and
    # review/permission anchors are source content too.
    if any(child.tag not in {qn('w:pPr'), qn('w:r')} for child in node):
        return False
    if any(child.tag not in {qn('w:rPr'), qn('w:t'), qn('w:tab')}
           for run in node.findall(qn('w:r')) for child in run):
        return False
    from api.services.tenders.word_structure import outline_level
    if outline_level(doc, node):
        return False
    para = Paragraph(node, doc._body)
    style = para.style
    while style is not None:
        if style.paragraph_format.page_break_before:
            return False
        style = style.base_style
    return not para.paragraph_format.page_break_before


def _slot_metrics_cm(doc, node):
    """Content height and spacing, including style/document defaults."""
    if node.tag == qn('w:tbl'):
        return _tbl_cm(node), 0.0, 0.0
    if node.tag != qn('w:p'):
        return 0.0, 0.0, 0.0
    para = Paragraph(node, doc._body)
    formats = [para.paragraph_format]
    style = para.style
    while style is not None:
        formats.append(style.paragraph_format)
        style = style.base_style
    defaults = doc.styles.element.find('w:docDefaults/w:pPrDefault/w:pPr', doc.styles.element.nsmap)
    if defaults is not None:
        default_para = OxmlElement('w:p')
        default_para.append(deepcopy(defaults))
        formats.append(Paragraph(default_para, doc._body).paragraph_format)
    def value(name, default):
        return next((getattr(fmt, name) for fmt in formats if getattr(fmt, name) is not None), default)
    before = value('space_before', Pt(0)).cm
    after = value('space_after', Pt(0)).cm
    spacing = value('line_spacing', 1.0)
    font_pt = max([run.font.size.pt for run in para.runs if run.font.size is not None] or [11.0])
    text = para.text
    section = doc.sections[-1]
    width_pt = (section.page_width - section.left_margin - section.right_margin) / 12700
    width_pt -= sum(value(name, Cm(0)).pt for name in ('left_indent', 'right_indent'))
    units_per_line = max(1, width_pt / font_pt)
    # Full-width scripts and Latin prose occupy different widths. A fixed
    # character count overestimates short labels and consumes reserved space.
    lines = sum(max(1, ceil(sum(1.0 if unicodedata.east_asian_width(c) in {'W','F'} else .65
                              for c in line) / units_per_line))
                for line in text.split('\n'))
    line = spacing.cm if hasattr(spacing, 'cm') else font_pt * 2.54 / 72 * 1.2 * float(spacing)
    drawn = _drawing_cm(node)
    borders = node.find('w:pPr/w:pBdr', node.nsmap)
    padding = 0.0
    if borders is not None:
        for side in ('top', 'bottom'):
            edge = borders.find(qn('w:' + side))
            if edge is not None:
                padding += (float(edge.get(qn('w:space'),'0')) + float(edge.get(qn('w:sz'),'0')) / 8) * 2.54 / 72
    return max(line * lines, drawn) + padding, before, after


def _slot_height_cm(doc, node):
    return sum(_slot_metrics_cm(doc, node))


def _slot_flow_cm(doc, nodes, previous=None, following=None):
    """Word collapses adjacent paragraph spacing to their maximum."""
    after = _slot_metrics_cm(doc, previous)[2] if previous is not None else 0.0
    height = 0.0
    for node in nodes:
        content, before, new_after = _slot_metrics_cm(doc, node)
        height += max(after, before) + content
        after = new_after
    before = _slot_metrics_cm(doc, following)[1] if following is not None else 0.0
    return height + max(after, before)


def _reuse_slot_blanks(doc, blanks, material, *, previous=None, following=None):
    """Replace blank paragraphs, retaining their total estimated flow height."""
    if not material or len(material) > len(blanks) or any(node.tag != qn('w:p') for node in material):
        return False
    available = _slot_flow_cm(doc, blanks, previous, following)
    remaining = len(blanks) - len(material)
    candidates = [deepcopy(node) for node in material]
    for _ in range(remaining):
        empty = OxmlElement('w:p')
        pf = Paragraph(empty, doc._body).paragraph_format
        pf.line_spacing = Pt(.05)  # one Word twip, not a zero-height paragraph
        pf.space_before = pf.space_after = Pt(0)
        pf.keep_with_next = False
        candidates.append(empty)
    need = _slot_flow_cm(doc, candidates, previous, following)
    if available < need:
        return False
    for target, source in zip(blanks, candidates):
        for child in list(target):
            target.remove(child)
        for child in source:
            target.append(deepcopy(child))
    surplus = available - need
    if remaining:
        for node in blanks[len(material):]:
            for child in list(node):
                node.remove(child)
            pf = Paragraph(node, doc._body).paragraph_format
            pf.line_spacing = Cm(1 / _TWIPS_PER_CM + surplus / remaining)
            pf.space_before = pf.space_after = Pt(0)
            pf.keep_with_next = False
    else:
        para = Paragraph(blanks[-1], doc._body)
        trailing = _slot_metrics_cm(doc, blanks[-1])[2]
        next_before = _slot_metrics_cm(doc, following)[1] if following is not None else 0.0
        if surplus:
            para.paragraph_format.space_after = Cm(max(trailing, next_before) + surplus)
    return True


def _drop_on_chapter(doc: Document, heading, slot: PlaceholderItem, files: list[Path]) -> bool:
    return _drop_after(doc, _chapter_last(heading._element), slot, files, complete=True)


_QUAL_VETO_MARKS = ("资质证明", "资质证书", "企业资质", "资质文件")
_OTHER_CHAPTERS = {"其他资料", "其他材料", "其他有必要提供的材料", "其他有必要提供的资料"}
_OTHER_PREFIX = re.compile(
    r"^(?:[（(][一二三四五六七八九十0-9]+[)）]|[一二三四五六七八九十0-9]+[、.])"
)
_SUB_HEAD = re.compile(r"^[（(][一二三四五六七八九十0-9]+[)）]")
_CHAPTER_LEVEL = re.compile(
    r"^(?:第[一二三四五六七八九十0-9]+[章节]|[一二三四五六七八九十]{1,3}、)"
)
_INPLACE = "此处应粘贴对应的资料附件或图片"
_NONE_TITLE = "资格审查其他材料无"
_STRONG_CUES = (
    "应在此提供",
    "在此提供",
    "在此附",
    "本表后应附",
    "本表后附",
    "后应附",
    "应附",
    "须附",
    "需附",
    "应提供",
    "需提供",
    "须提供",
    "粘贴",
    "复印件或扫描件",
    "扫描件",
)
_NEAR_CUES = ("材料", "证明", "报表", "证书", "合同", "发票", "执照", "截图", "审计", "纳税", "社保")


def _wants_attach(text: str) -> bool:
    n = _compact_para(text)
    if not n or "此处粘贴" in n or _INPLACE in n:
        return False
    if n.startswith("注") and "附" in n:
        return True
    if any(k in n for k in _STRONG_CUES):
        return True
    i = 0
    while True:
        j = n.find("提供", i)
        if j < 0:
            return False
        window = n[max(0, j - 12) : j + 14]
        if any(k in window for k in _NEAR_CUES):
            return True
        i = j + 2


def _paste_ban(text: str) -> bool:
    n = _compact_para(text)
    if not n or len(n) > 36 or "。" in n:
        return False
    if any(x in n for x in ("或", "；", ";", "，", "须", "提供")):
        return False
    n = re.sub(r"[（(]如有[)）]", "", n)
    n = _OTHER_PREFIX.sub("", n)
    n = re.sub(r"^第[一二三四五六七八九十0-9]+[章节]", "", n)
    n = re.sub(r"^[一二三四五六七八九十]{1,3}、", "", n)
    if n in _NO_PASTE:
        return True
    return n.startswith("投标函") and len(n) <= 16


def _qual_mark(text: str) -> str:
    n = "".join((text or "").split())
    for mark in _QUAL_VETO_MARKS:
        if mark in n:
            return mark
    return ""


_SCATTER_CUES = (
    "应附",
    "须附",
    "需附",
    "应提供",
    "须提供",
    "需提供",
    "应包含",
    "须包含",
    "复印件",
    "扫描件",
)
_NAME_CUT = re.compile(
    r"(?:应附|须附|需附上|需附|应提供|须提供|需提供|应包含|须包含)"
    r"([^，。；;、（(]{2,18})"
)


def _clip_material_name(text: str, target: str) -> str:
    title = "".join((target or "").split())
    if 2 <= len(title) <= 18:
        return title
    n = "".join((text or "").split())
    hit = _NAME_CUT.search(n)
    if hit is None:
        return ""
    name = hit.group(1)
    for tail in ("复印件", "扫描件", "原件"):
        if name.endswith(tail) and len(name) > len(tail):
            name = name[: -len(tail)]
    if len(name) < 2 or len(name) > 18:
        return ""
    return name


def _merge_scattered_materials(
    by_key: dict[str, PlaceholderItem],
    keys: list[str],
    requirements,
    vol_of: dict[str, str],
    volume: str,
) -> list[str]:
    from api.services.tenders.match import (
        _clean_attach_section,
        find_catalog_item,
        same_library_material,
        scan_slot_key,
    )

    vol = (volume or "").strip().lower()
    have = set(keys)
    added: list[str] = []
    seen_names: set[str] = set()
    catalog = list(by_key.values())
    for item in requirements or []:
        text = getattr(item, "text", "") or ""
        n = "".join(text.split())
        if not any(cue in n for cue in _SCATTER_CUES):
            continue
        if n.startswith(("（", "(")) and "如有" in n:
            continue
        if (getattr(item, "matchStatus", "") or "") == "format":
            continue
        if (getattr(item, "targetKind", "") or "") == "format":
            continue
        if (getattr(item, "strength", "") or "") == "mandatory" and _qual_mark(text):
            continue
        item_vol = (getattr(item, "volume", "") or "").strip().lower()
        if vol in {"business", "technical"} and item_vol in {"business", "technical"} and item_vol != vol:
            continue
        name = _clip_material_name(text, getattr(item, "targetTitle", "") or "")
        if not name or name in seen_names:
            continue
        if any(same_library_material(name, (by_key[k].title or "")) for k in have if k in by_key):
            seen_names.add(name)
            continue
        sk = (getattr(item, "slotKey", "") or "").strip()
        found = None
        if sk and sk in by_key:
            found = by_key[sk]
        if found is None:
            found = find_catalog_item(catalog, key=sk, title=name, hint="")
        key = (found.key or "").strip() if found is not None else ""
        if not key:
            guessed = scan_slot_key(name, catalog)
            if guessed:
                key = guessed
                found = by_key.get(guessed)
        if key and key in have:
            seen_names.add(name)
            continue
        if not key:
            key = "scatter_" + name
        if key in have:
            seen_names.add(name)
            continue
        section = _clean_attach_section(getattr(item, "attachToSection", "") or "")
        if found is None or key not in by_key:
            by_key[key] = PlaceholderItem(
                key=key,
                title=(found.title if found is not None and found.title else name),
                hint="",
                attachToSection=section,
            )
            catalog.append(by_key[key])
        elif section and not (by_key[key].attachToSection or "").strip():
            by_key[key].attachToSection = section
        keys.append(key)
        have.add(key)
        seen_names.add(name)
        if item_vol in {"business", "technical"}:
            vol_of[key] = item_vol
        added.append(by_key[key].title or name)
    return added


def _merge_unmatched_qual(by_key: dict[str, PlaceholderItem], keys: list[str], requirements) -> None:
    """否决项里的资质证明没对上资料时，补一个挂在「资格审查其他材料」的空位。"""
    seen: set[str] = set()
    have = set(keys)
    for item in requirements or []:
        if (getattr(item, "strength", "") or "") != "mandatory":
            continue
        if (getattr(item, "matchStatus", "") or "") == "matched":
            continue
        if (getattr(item, "volume", "") or "") == "technical":
            continue
        mark = _qual_mark(getattr(item, "text", "") or "")
        if not mark or mark in seen:
            continue
        sk = (getattr(item, "slotKey", "") or "").strip()
        if sk and sk in have and sk in by_key:
            seen.add(mark)
            continue
        seen.add(mark)
        key = f"veto_{mark}"
        if key in by_key:
            if key not in have:
                keys.append(key)
            continue
        by_key[key] = PlaceholderItem(
            key=key,
            title=mark,
            hint="否决项要求提供，未匹配到扫描件，请在此补",
            attachToSection="资格审查其他材料",
        )
        keys.append(key)


def _is_other_title(text: str) -> bool:
    n = _compact_para(text)
    if not n or len(n) > 28 or "。" in n or "如有" in n:
        return False
    n = _OTHER_PREFIX.sub("", n)
    if n in _OTHER_CHAPTERS:
        return True
    return n.startswith("其他有必要提供") and len(n) <= 16


def _already_placed(doc: Document, slot: PlaceholderItem) -> bool:
    title = _compact_para(slot.title)
    if len(title) < 4:
        return False
    for para in doc.paragraphs:
        if _note_score(title, para.text or "") >= 1000 and _filled_slot_mark(para._p):
            return True
    for tbl in doc.element.body.iter(qn("w:tbl")):
        blob = "".join(t.text or "" for t in tbl.iter(qn("w:t")))
        if _note_score(title, blob) >= 1000 and _filled_slot_mark(tbl):
            return True
    if (slot.key or "") != "id_legal" and "身份证" not in title:
        return False
    # 身份证明页已经贴过图或虚线框，卷末和其他资料里不再来一份
    for para in doc.paragraphs:
        n = _compact_para(para.text or "")
        if "法定代表人身份证" not in n or "委托" in n:
            continue
        if not (n.startswith("附") or "正反面" in n):
            continue
        sib = para._p.getnext()
        for _ in range(6):
            if sib is None or (sib.tag == qn("w:p") and _heading_core(_para_text(sib))):
                break
            xml = sib.xml
            if "w:drawing" in xml or "w:pict" in xml or "此处粘贴" in xml:
                return True
            sib = sib.getnext()
    return False


def _filled_slot_mark(el) -> bool:
    if _is_slot_box(el) or next(el.iter(qn("w:drawing")), None) is not None:
        return True
    return el.tag == qn("w:tbl") and any(
        border.get(qn("w:val")) in {"dashed", "dotted"}
        for border in el.iter(qn("w:top"))
    )


def _other_heading(doc: Document, toc_els: list):
    for para in doc.paragraphs:
        if para._p in toc_els:
            continue
        if _is_other_title(para.text or ""):
            return para
    return None


def _slot_marks(slot: PlaceholderItem) -> list[str]:
    title = _compact_para(slot.title)
    hint = _compact_para(slot.hint)
    blob = title + hint
    marks: list[str] = []
    sec = _level1_core(slot.attachToSection) or _compact_para(slot.attachToSection)
    if (
        sec
        and sec not in _OTHER_CHAPTERS
        and sec not in _NO_PASTE
        and "如有" not in sec
        and not _paste_ban(slot.attachToSection or "")
    ):
        marks.append(sec)
        # 章节标题同义：邀请书可能写全称，原版章节只写简称。
        _SECTION_ALIASES = (
            ("法定代表人授权委托书", "授权委托书"),
            ("授权委托书", "法定代表人授权委托书"),
            ("财务审计报告", "财务审计"),
            ("财务报表", "财务审计"),
            ("财务状况", "财务审计"),
            ("业绩合同", "类似项目"),
            ("业绩", "类似项目"),
            ("资质证书", "资质证明"),
            ("资质证明", "资质证书"),
            ("营业执照", "营业执照"),
            ("开户许可", "开户许可证"),
        )
        for left, right in _SECTION_ALIASES:
            if left in sec and right not in marks:
                marks.append(right)
    key = (slot.key or "").strip()
    if key in _KEY_CHAPTER:
        marks.append(_KEY_CHAPTER[key])
    if key == "credit":
        marks.append("国家企业信用信息公示系统")
    elif key == "finance":
        marks.append("财务状况")
    for mark in _CHAPTER_MARKS:
        if mark in blob:
            marks.append(mark)
    if 4 <= len(title) <= 16:
        marks.append(title)
    marks.sort(key=len, reverse=True)
    out: list[str] = []
    for mark in marks:
        if mark and mark not in out:
            out.append(mark)
    return out


def _locate_chapter(doc: Document, slot: PlaceholderItem, toc_els: list, letter_els: list | None = None):
    """对上正文专章就用那一章。目录、投标函和「如有」列举处不算。"""
    banned = letter_els or []
    best = None
    best_n = 0
    label = ""
    for para in doc.paragraphs:
        if para._p in toc_els or para._p in banned or _paste_ban(para.text or ""):
            continue
        source = _section_label(para._p)
        core = _level1_core(source) or (_SUB_HEAD.sub("", source) if _SUB_HEAD.match(source) else _heading_core(source))
        if not core or core in _NO_PASTE or core in _OTHER_CHAPTERS or core.startswith("投标函"):
            continue
        if "工作经历" in core or "简历" in core:
            continue
        for mark in _slot_marks(slot):
            if mark in core or core in mark:
                if len(core) > best_n:
                    best = para
                    best_n = len(core)
                    label = _compact_para(para.text or "")
                break
    return best, label


def _real_chapter(text: str) -> bool:
    n = _compact_para(text)
    if not n or len(n) > 36 or "。" in n or "如有" in n or n.startswith(("（", "(")):
        return False
    if not _CHAPTER_LEVEL.match(n):
        return False
    if _paste_ban(text) or _is_other_title(text):
        return False
    return True


def _form_els(doc: Document) -> set:
    banned = set(_letter_block(doc))
    inside = False
    for child in doc.element.body:
        if child.tag == qn("w:sectPr"):
            break
        if child.tag == qn("w:p"):
            text = _para_text(child)
            if _paste_ban(text):
                inside = True
                banned.add(child)
                continue
            if inside and (
                _real_chapter(_section_label(child))
                or (_SUB_HEAD.match(_compact_para(_section_label(child))) and not _paste_ban(text))
                or (_heading_core(text) and not _paste_ban(text))
            ):
                inside = False
                continue
            if inside:
                banned.add(child)
        elif inside and child.tag == qn("w:tbl"):
            banned.add(child)
    return banned


def _note_score(title: str, text: str) -> int:
    name = _compact_para(title)
    n = _compact_para(text)
    if len(name) < 4:
        return 0
    for marker in re.finditer(r"此处粘贴([^。]{1,80})", n):
        material = re.split(r"[（(\n]在此粘贴|[（(\n]|[；;。]", marker.group(1), maxsplit=1)[0]
        compared_name = name.replace("财务会计报表", "财务报表")
        compared_material = material.replace("财务会计报表", "财务报表")
        if compared_name in compared_material or (
            len(compared_material) >= 4 and compared_material in compared_name
        ):
            return 1000 + min(len(name), len(material))
    if not _wants_attach(n):
        return 0
    if name in n:
        return len(name)
    hit = re.search(r"(?:此处粘贴|在此附|本表后应附|本表后附|后应附|应附|应提供|需提供|须提供|须附|需附)([^。]{0,80})", n)
    if hit is None:
        return 0
    best = 0
    raw = re.split(r"[；;]", hit.group(1))[0]
    for part in re.split(r"或|、|及|和", raw):
        part = re.sub(r"(复印件|扫描件|原件|等)$", "", part).strip("（）()：:，,")
        if len(part) < 4:
            continue
        if part in name or name in part:
            best = max(best, min(len(part), len(name)))
    return best


def _near_label(el) -> str:
    prev = el.getprevious()
    while prev is not None:
        if prev.tag == qn("w:tbl"):
            break
        if prev.tag == qn("w:p"):
            text = _compact_para(_para_text(prev))
            if text and not text.startswith("注") and "。" not in text and len(text) <= 32:
                return text
        prev = prev.getprevious()
    return ""


def _locate_note(doc: Document, slot: PlaceholderItem, banned: set, toc_els: list, *, section_head=None, section_body=None):
    """Specified site -> blank run -> table end -> owned section end only."""
    from api.services.tenders.section_materials import sections, section_type, attachment_anchors, _heading_text, _key
    scopes = [(section_head,section_body or [],0)] if section_head is not None else sections(doc,set(banned)|set(toc_els))
    for head, body, _ in scopes:
        if section_head is None:
            wanted=_key(slot.attachToSection or slot.title)
            heading=_key(_heading_text(head))
            from difflib import SequenceMatcher
            if not wanted or not heading or (wanted!=heading and SequenceMatcher(None,wanted,heading).ratio()<0.72):
                continue
        if section_type(head,body)=='A':
            return None,''
        blocks=[_body_block(el) for el in body if el not in banned and el not in toc_els and _body_block(el).getparent() is doc.element.body]
        if not blocks:
            blocks=[head]
        order=list(doc.element.body)
        tables=[el for el in blocks if next(el.iter(qn('w:tbl')),None) is not None]
        floor=max((order.index(el) for el in tables),default=order.index(head))
        sites=attachment_anchors(body)
        matching=[site for site in sites if _note_score(slot.title,_para_text(site))>0 or (slot.title and slot.title in _para_text(site))]
        if not matching and section_type(head,body)=='B':
            matching=sites
        for site in matching:
            block=_body_block(site)
            if order.index(block)>=floor:return block,_heading_text(head)
        blanks=[]
        for block in blocks:
            blank=(block.tag==qn('w:p') and not _para_text(block).strip()
                   and next(block.iter(qn('w:drawing')),None) is None
                   and next(block.iter(qn('w:br')),None) is None
                   and next(block.iter(qn('w:sectPr')),None) is None)
            if blank and order.index(block)>=floor:
                blanks.append(block)
                if len(blanks)>=2:return block,_heading_text(head)
            else:blanks=[]
        return blocks[-1],_heading_text(head)
    return None, ""


def _sec_head(text: str) -> bool:
    n = _compact_para(text)
    if not n or len(n) > 36 or "。" in n:
        return False
    if _paste_ban(text):
        return True
    if "如有" in n or n.startswith("注"):
        return False
    if _real_chapter(text) or _heading_core(text):
        return True
    return bool(_SUB_HEAD.match(n))


def _section_label(el) -> str:
    """原格式章的编号常只存在于标题书签中，浮动表格也可作为章节边界。"""
    for mark in el.iter(qn("w:bookmarkStart")):
        name = mark.get(qn("w:name"), "")
        if _real_chapter(name) or _SUB_HEAD.match(name):
            return name
    return _para_text(el)


def _owner(el):
    cur = el
    while cur is not None:
        if cur.tag == qn("w:p") and _sec_head(_section_label(cur)):
            return cur
        cur = cur.getprevious()
    return None


def _span_taken(head) -> bool:
    el = head.getnext()
    while el is not None and el.tag != qn("w:sectPr"):
        if el.tag == qn("w:p") and _sec_head(_section_label(el)):
            break
        blob = "".join(t.text or "" for t in el.iter(qn("w:t")))
        flat = "".join(blob.split())
        if "此处粘贴" in flat or _INPLACE in flat or _NONE_TITLE in flat:
            return True
        if el.tag == qn("w:p") and ("w:drawing" in el.xml or "w:pict" in el.xml):
            return True
        el = el.getnext()
    return False


def _span_last(head):
    last = head
    el = head.getnext()
    while el is not None and el.tag != qn("w:sectPr"):
        if el.tag == qn("w:p") and _sec_head(_section_label(el)):
            break
        if el.tag in {qn("w:p"), qn("w:tbl")}:
            last = el
        el = el.getnext()
    return last


def _cue_anchor(head):
    found = None
    el = head.getnext()
    while el is not None and el.tag != qn("w:sectPr"):
        if el.tag == qn("w:p") and _sec_head(_section_label(el)):
            break
        if el.tag == qn("w:p"):
            text = _para_text(el)
        elif el.tag == qn("w:tbl"):
            text = "".join(t.text or "" for t in el.iter(qn("w:t")))
        else:
            el = el.getnext()
            continue
        if _wants_attach(text):
            flat = _compact_para(text)
            if flat.startswith("注") or "注：" in flat or "注:" in flat:
                return el
            if found is None:
                found = el
        el = el.getnext()
    return found


def _embed_files(slot: PlaceholderItem, media, key: str) -> list[Path]:
    out = []
    for item in (media or {}).get(key) or []:
        path = item if isinstance(item, Path) else Path(str(item))
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        if path.suffix.lower() in _IMAGE_EXT | {".pdf"}:
            out.append(path)
    import logging
    logging.getLogger(__name__).info('material embed key=%s mapping_present=%s input=%s usable=%s paths=%s',key,key in (media or {}),len((media or {}).get(key) or []),len(out),[str(p) for p in out])
    return out


def _files_for_blurb(text: str, by_key: dict, media):
    from api.services.tenders.match import find_catalog_item, scan_slot_key

    name = _clip_material_name(text, "")
    if not name:
        hit = re.search(
            r"(?:在此附|后应附|应附|应提供|需提供|须提供)([^，。；;、（(]{2,18})",
            _compact_para(text),
        )
        name = hit.group(1) if hit else ""
    if len(name) < 2:
        return [], None
    catalog = list(by_key.values())
    found = find_catalog_item(catalog, title=name, hint="")
    key = (found.key or "").strip() if found is not None else ""
    if not key:
        key = scan_slot_key(name, catalog)
        if key:
            found = by_key.get(key)
    if not key:
        return [], None
    slot = found if found is not None else PlaceholderItem(key=key, title=name)
    return _embed_files(slot, media, key), slot


def _review_plate(doc: Document, toc_els: list, ban: set):
    for para in doc.paragraphs:
        if para._p in toc_els or para._p in ban:
            continue
        n = _compact_para(para.text or "")
        if "资格审查其他材料" in n and len(n) <= 28 and "。" not in n:
            return para
    return None


def _write_none_letter(doc: Document, head) -> str:
    if _span_taken(head):
        return ""
    last = _span_last(head)
    marker = _last_body_child(doc)
    doc.add_paragraph(_NONE_TITLE)
    doc.add_paragraph("我方无需要补充的其他资格审查材料。")
    _move_after(doc, last, marker)
    return "「资格审查其他材料」栏为空，已补无声明"


def place_unmatched_slots(
    doc: Document,
    *,
    slots: list[PlaceholderItem] | None,
    media: dict | None,
    required_keys: list[str] | None,
    volume: str = "",
    skip_keys: set[str] | None = None,
    requirements: list | None = None,
    global_pasted: set[str] | None = None,
) -> list[str]:
    """Preserve source blocks; append supplements only to material-list sections."""
    from api.services.tenders.pdf_format import toc_block_ids
    from api.services.tenders.section_materials import append_section_materials

    catalog = {slot.key: slot for slot in slots or []}
    keys = list(required_keys or [])
    for requirement in requirements or []:
        # Only evidence-bound requirements belong to the attachment renderer;
        # text responses and fixed forms are handled by their original fields.
        key = (getattr(requirement, 'slotKey', '') or '').strip()
        if not key or getattr(requirement, 'targetKind', '') == 'format':
            continue
        requirement_volume = (getattr(requirement, 'volume', '') or '').strip()
        if volume and requirement_volume and requirement_volume != volume:
            continue
        if key not in catalog:
            name = (getattr(requirement, 'targetTitle', '') or '').strip()
            if name:
                catalog[key] = PlaceholderItem(
                    key=key, title=name,
                    attachToSection=getattr(requirement, 'attachToSection', '') or '',
                )
        if key not in keys:
            keys.append(key)
    notes, handled = append_section_materials(
        doc, list(catalog.values()), media, keys, toc_block_ids(doc),
        set(skip_keys or ()) | set(global_pasted or ()),
    )
    if global_pasted is not None:
        global_pasted.update(handled)
    return notes


def draw_placeholder_box(doc: Document, slot: PlaceholderItem | None = None, *, after=None) -> None:
    slot = slot or PlaceholderItem(key='pending',title='待补资料')
    if after is not None:
        anchor = after._element if hasattr(after,'_element') else after
    else:
        from api.services.tenders.pdf_format import toc_block_ids
        anchor,_ = _locate_note(doc,slot,set(),toc_block_ids(doc))
    if anchor is None:
        raise ValueError('No owned chapter boundary for material '+slot.title)
    _drop_after(doc,anchor,slot,[],complete=True,at_marker=True)


def fill_perf_placeholders(table: Table) -> None:
    """企业业绩表空单元格写入待补说明，避免空白被误认为已填。"""
    if not table.rows:
        return
    start = 1 if len(table.rows) > 1 else 0
    for row in table.rows[start:]:
        for cell in row.cells:
            compact = "".join((cell.text or "").split())
            if compact and compact not in {"／", "/", "—", "-", "……", "..."}:
                continue
            _write_plain(cell, _PERF_NOTE)


def _ordinal(index: int) -> str:
    if 0 <= index < len(_CN_ORD):
        return _CN_ORD[index]
    return str(index + 1)


def _page_body_cm(doc: Document) -> float:
    sec = doc.sections[-1]
    return max(
        16.0,
        float(sec.page_height.cm) - float(sec.top_margin.cm) - float(sec.bottom_margin.cm) - 0.6,
    )


def _drawing_cm(el) -> float:
    heights = []
    for node in el.iter():
        if node.tag.split("}")[-1] != "extent":
            continue
        cy = node.get("cy")
        if cy:
            heights.append(int(cy) / _EMU_PER_CM)
    # The two compact identity faces share one line, not two stacked rows.
    if el.tag == qn('w:p') and next(el.iter(qn('w:tab')), None) is not None:
        return max(heights, default=0.0)
    return sum(heights)


def _tbl_cm(tbl) -> float:
    total = 0.0
    for tr in tbl.findall(qn("w:tr")):
        row_cm = 0.0
        tr_pr = tr.find(qn("w:trPr"))
        if tr_pr is not None:
            height = tr_pr.find(qn("w:trHeight"))
            if height is not None:
                raw = height.get(qn("w:val"))
                if raw:
                    row_cm = int(raw) / _TWIPS_PER_CM
        total += row_cm if row_cm > 0 else 0.72
    return max(total, 0.72)


def _used_on_page_cm(doc: Document) -> float:
    used = 0.0
    for child in doc.element.body:
        if child.tag == qn("w:sectPr"):
            continue
        if child.tag == qn("w:tbl"):
            used += _tbl_cm(child)
            continue
        if child.tag != qn("w:p"):
            continue
        if _is_slot_gap(child):
            used += _SLOT_GAP_CM
            continue
        if any(br.get(qn("w:type")) == "page" for br in child.iter(qn("w:br"))):
            used = 0.35
            continue
        p_pr = child.find(qn("w:pPr"))
        if p_pr is not None and p_pr.find(qn("w:sectPr")) is not None:
            used = 0.35
        if p_pr is not None:
            page_before=p_pr.find(qn('w:pageBreakBefore'))
            if page_before is not None and page_before.get(qn('w:val'),'1') not in {'0','false','off'}:
                used=0.0
        if next(child.iter(qn('w:lastRenderedPageBreak')),None) is not None:
            used=0.0
        drawn = _drawing_cm(child)
        if drawn:
            used += min(drawn + 0.35, 20.0)
            continue
        text = "".join(node.text or "" for node in child.iter(qn("w:t"))).replace("\u200b", "").strip()
        if p_pr is not None:
            sp = p_pr.find(qn("w:spacing"))
            if sp is not None and (sp.get(qn("w:lineRule")) or "") == "exact":
                line = int(sp.get(qn("w:line")) or 0)
                if line:
                    used += line / _TWIPS_PER_CM
                    continue
        if not text:
            used += 0.22
            continue
        lines = max(1, (len(text) + 29) // 30)
        used += 0.48 * lines + 0.22
    return used


def _first_media_cm(files: list[Path], *, width_cm: float = 15.5, max_h: float | None = None) -> float:
    cap = _IMAGE_MAX_H_CM if max_h is None else max_h
    for path in files:
        suf = path.suffix.lower()
        if suf == ".pdf":
            return min(cap, width_cm * 297 / 210)
        if suf in _IMAGE_EXT and path.is_file():
            try:
                from PIL import Image

                with Image.open(path) as image:
                    w, h = image.size
                if w > 0:
                    return min(cap, max(3.5, width_cm * h / float(w)))
            except Exception:
                return 10.0
    return _BOX_CM


def _break_before_slot(doc: Document, need_cm: float) -> None:
    """Move only new material to a fresh page when the current page is full."""
    body_cm = _page_body_cm(doc)
    used = _used_on_page_cm(doc) % body_cm
    remain = body_cm - used
    if remain + 0.2 >= need_cm:
        return
    if used <= 0.6:
        return
    doc.add_page_break()


def _write_subheading(doc: Document, index: int, title: str) -> None:
    para = doc.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    para.paragraph_format.space_before = Pt(6)
    para.paragraph_format.space_after = Pt(6)
    para.paragraph_format.keep_with_next = True
    run = para.add_run(f"（{_ordinal(index)}）{title}")
    _font(run, 14, bold=True)


def _is_slot_gap(el) -> bool:
    if el is None or el.tag != qn("w:p"):
        return False
    style = el.find("w:pPr/w:pStyle", el.nsmap)
    return style is not None and style.get(qn("w:val")) == _SLOT_GAP_STYLE


def _is_slot_clearance(el) -> bool:
    if el is None or el.tag != qn("w:p"):
        return False
    style = el.find("w:pPr/w:pStyle", el.nsmap)
    return style is not None and style.get(qn("w:val")) == _SLOT_CLEARANCE_STYLE


def _is_slot_box(el) -> bool:
    return el is not None and el.tag == qn("w:p") and el.find("w:pPr/w:pBdr", el.nsmap) is not None and (
        "此处粘贴" in _para_text(el) or _INPLACE in _para_text(el)
    )


def _merge_slot_gaps(doc: Document) -> None:
    """相邻附件共用一个留白段，不叠加成 1 cm。"""
    previous_gap = False
    for node in list(doc.element.body):
        gap = _is_slot_gap(node)
        if gap and previous_gap:
            doc.element.body.remove(node)
        previous_gap = gap


def _space_slot_block(doc: Document, block) -> None:
    """只在新增附件外放 0.5 cm 空段；不修改相邻原文或表格。"""
    if _SLOT_GAP_STYLE not in doc.styles:
        style = doc.styles.add_style(_SLOT_GAP_STYLE, WD_STYLE_TYPE.PARAGRAPH)
        style.font.size = Pt(1)
        pf = style.paragraph_format
        pf.space_before = pf.space_after = Pt(0)
        pf.line_spacing = Cm(_SLOT_GAP_CM)
        pf.left_indent = pf.right_indent = pf.first_line_indent = Cm(0)
        pf.keep_with_next = False
        pf.page_break_before = False
    for before in (True, False):
        gap = doc.add_paragraph(style=_SLOT_GAP_STYLE)
        # 留白本身就是正文空段，直接写高度，避免依赖阅读器解析自定义样式。
        gap.paragraph_format.space_before = gap.paragraph_format.space_after = Pt(0)
        gap.paragraph_format.line_spacing = Cm(_SLOT_GAP_CM)
        gap.add_run().font.size = Pt(1)
        # 身份证图原有的“与下段同页”继续连到签章段，不能被尾部留白截断。
        keep_next = block.tag == qn("w:p") and Paragraph(block, doc._body).paragraph_format.keep_with_next is True
        gap.paragraph_format.keep_with_next = before or keep_next
        if before:
            block.addprevious(gap._p)
        else:
            block.addnext(gap._p)


def _draw_box(doc: Document, slot: PlaceholderItem, *, wording: str = "") -> None:
    """普通文字段落带虚线边框，选中整段（含段落标记）即可 Delete 删除。"""
    mark = doc.add_paragraph()
    mark.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pf = mark.paragraph_format
    pf.left_indent = pf.right_indent = pf.first_line_indent = Cm(0)
    pf.space_before = pf.space_after = Pt(0)
    pf.line_spacing = Pt(12)
    pf.keep_together = True
    pf.keep_with_next = False
    pf.page_break_before = False
    borders = OxmlElement("w:pBdr")
    for edge in ("top", "left", "bottom", "right"):
        border = OxmlElement("w:" + edge)
        for name, value in (("val", "dashed"), ("sz", "18"), ("space", "8"), ("color", "7A7A7A")):
            border.set(qn("w:" + name), value)
        borders.append(border)
    mark._p.get_or_add_pPr().append(borders)
    title = (slot.title or "").strip()
    line = wording or (f"此处粘贴{title}" if title else "")
    text = line if wording else line + "\n（在此粘贴扫描件）"
    run = mark.add_run(text)
    _font(run, 10.5, bold=False)
    run.font.color.rgb = RGBColor(0x7A, 0x7A, 0x7A)
    # Natural wrapping determines height. Keep the labelled frame together,
    # without eight filler lines or additional half-centimetre spacer blocks.
    pf.space_before = pf.space_after = Pt(4)


def _insert_slot_media(
    doc: Document,
    files: list[Path],
    *,
    budget: dict[str, int] | None = None,
    max_pages: int = _SLOT_PDF_MAX_PAGES,
    compact_pair: bool = False,
    force_landscape: bool = False,
    sharp: bool = False,
) -> int:
    original_nodes = set(doc.element.body)
    if compact_pair:
        if budget is not None and budget.get('left', 0) <= 0:
            return 0
        prepared = _collect_scan_jpegs(
            files,
            # A selected identity is one material result. Its front/back must
            # not be split by a shared image-count budget ending at one face.
            budget=None,
            max_images=_ID_PAIR_MAX_IMAGES,
            max_pages=max(_SLOT_PDF_MAX_PAGES, _ID_PAIR_MAX_IMAGES),
            force_landscape=force_landscape,
        )
        inserted = _insert_id_cards(doc, prepared)
        if budget is not None:
            budget['left'] = max(0, int(budget['left']) - inserted)
        _space_new_slot_media(doc, original_nodes)
        return inserted

    inserted = 0
    for path in files:
        if budget is not None and budget.get("left", 0) <= 0:
            break
        suf = path.suffix.lower()
        try:
            if suf == ".pdf":
                n = _insert_pdf_pages(doc, path, max_pages=max_pages, budget=budget, sharp=sharp)
                inserted += n
            elif suf in _IMAGE_EXT:
                if budget is not None and budget.get("left", 0) <= 0:
                    break
                if _insert_image(doc, path, sharp=sharp):
                    inserted += 1
                    if budget is not None:
                        budget["left"] = max(0, int(budget["left"]) - 1)
        except Exception:
            logger.exception("insert slot media failed: %s", path)
    _space_new_slot_media(doc, original_nodes)
    return inserted


def _space_new_slot_media(doc: Document, original_nodes: set) -> None:
    for node in list(doc.element.body):
        if node in original_nodes or node.tag != qn("w:p"):
            continue
        if next(node.iter(qn("w:drawing")), None) is None:
            continue
        # 外侧留白统一交给空段，避免与旧的图片段前/段后距离叠加。
        para = Paragraph(node, doc._body)
        para.paragraph_format.space_before = Pt(0)
        para.paragraph_format.space_after = Pt(0)
        _space_slot_block(doc, node)


def _render_pdf_page_jpeg(
    page,
    *,
    scale: float = _RENDER_SCALE,
    quality: int = _JPEG_QUALITY,
    max_px: int | None = None,
) -> io.BytesIO:
    bitmap = page.render(scale=scale)
    image = bitmap.to_pil().convert("RGB")
    image = _shrink_pil(image, max_px=_IMAGE_MAX_PX if max_px is None else max_px)
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality, optimize=False)
    buf.seek(0)
    return buf


def _shrink_pil(image, max_px: int | None = None):
    try:
        cap = _IMAGE_MAX_PX if max_px is None else int(max_px)
        w, h = image.size
        longest = max(w, h)
        if cap <= 0 or longest <= cap:
            return image
        from PIL import Image as PilImage

        ratio = cap / float(longest)
        resample = getattr(getattr(PilImage, "Resampling", PilImage), "LANCZOS", PilImage.BICUBIC)
        return image.resize((max(1, int(w * ratio)), max(1, int(h * ratio))), resample)
    except Exception:
        return image


def _apply_exif(image):
    try:
        from PIL import ImageOps

        transposed = ImageOps.exif_transpose(image)
        return transposed if transposed is not None else image
    except Exception:
        return image


def _looks_like_id_card(width: int, height: int) -> bool:
    short = min(width, height)
    long = max(width, height)
    if short <= 0:
        return False
    ratio = long / float(short)
    return _ID_ASPECT_MIN <= ratio <= _ID_ASPECT_MAX


def _force_landscape(image):
    from PIL import Image

    w, h = image.size
    if h <= w:
        return image
    rot = Image.Transpose.ROTATE_90 if hasattr(Image, "Transpose") else Image.ROTATE_90
    return image.transpose(rot)


def _normalize_id_image(image, *, force_landscape: bool):
    image = _apply_exif(image).convert("RGB")
    w, h = image.size
    if force_landscape or _looks_like_id_card(w, h):
        image = _force_landscape(image)
    return _shrink_pil(image)


def _image_to_jpeg(image) -> io.BytesIO:
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=max(_JPEG_QUALITY, 55), optimize=False)
    buf.seek(0)
    return buf


def _fit_cm(px_w: int, px_h: int, *, max_w: float, max_h: float) -> tuple[float, float]:
    if px_w <= 0 or px_h <= 0:
        return max_w, min(max_h, max_w * 0.63)
    aspect = px_w / float(px_h)
    width = max_w
    height = width / aspect
    if height > max_h:
        height = max_h
        width = height * aspect
    return width, height


def _collect_scan_jpegs(
    files: list[Path],
    *,
    budget: dict[str, int] | None,
    max_images: int,
    max_pages: int,
    force_landscape: bool,
) -> list[tuple[io.BytesIO, int, int]]:
    out: list[tuple[io.BytesIO, int, int]] = []
    for path in files[:_SLOT_MAX_FILES]:
        if len(out) >= max_images:
            break
        if budget is not None and budget.get("left", 0) <= 0:
            break
        suf = path.suffix.lower()
        try:
            if suf == ".pdf":
                for buf, w, h in _pdf_pages_as_jpegs(
                    path,
                    max_pages=max_pages,
                    force_landscape=force_landscape,
                    limit=max_images - len(out),
                ):
                    out.append((buf, w, h))
                    if budget is not None:
                        budget["left"] = max(0, int(budget["left"]) - 1)
                    if len(out) >= max_images:
                        break
                    if budget is not None and budget.get("left", 0) <= 0:
                        break
            elif suf in _IMAGE_EXT and path.is_file():
                from PIL import Image

                with Image.open(path) as raw:
                    image = _normalize_id_image(raw, force_landscape=force_landscape)
                    w, h = image.size
                    out.append((_image_to_jpeg(image), w, h))
                if budget is not None:
                    budget["left"] = max(0, int(budget["left"]) - 1)
        except Exception:
            logger.exception("prepare slot scan failed: %s", path)
    return out


def _pdf_pages_as_jpegs(
    pdf_path: Path,
    *,
    max_pages: int,
    force_landscape: bool,
    limit: int,
) -> list[tuple[io.BytesIO, int, int]]:
    if not pdf_path.is_file() or limit <= 0:
        return []
    try:
        import pypdfium2 as pdfium
    except ImportError:
        logger.warning("pypdfium2 missing, skip slot pdf %s", pdf_path.name)
        return []
    try:
        pdf = pdfium.PdfDocument(str(pdf_path))
    except Exception:
        logger.exception("open slot pdf failed: %s", pdf_path)
        return []
    pages: list[tuple[io.BytesIO, int, int]] = []
    count = min(len(pdf), max_pages, limit)
    for i in range(count):
        try:
            bitmap = pdf[i].render(scale=_RENDER_SCALE)
            image = _normalize_id_image(bitmap.to_pil(), force_landscape=force_landscape)
            w, h = image.size
            pages.append((_image_to_jpeg(image), w, h))
        except Exception:
            logger.exception("render slot pdf page %s of %s failed", i + 1, pdf_path.name)
    return pages


def _insert_id_cards(doc: Document, images: list[tuple[io.BytesIO, int, int]]) -> int:
    """Place both identity faces in one paragraph with a fixed tab gap."""
    if not images:
        return 0
    cards=images[:_ID_PAIR_MAX_IMAGES]
    sec=doc.sections[-1]
    available=(sec.page_width-sec.left_margin-sec.right_margin)/_EMU_PER_CM-0.4
    aspects=[w/float(h) for _,w,h in cards]
    gap=0.25 if len(cards)>1 else 0
    height=min(5.4,(available-gap)/sum(aspects))
    para=doc.add_paragraph()
    para.alignment=WD_ALIGN_PARAGRAPH.LEFT
    pf=para.paragraph_format
    pf.space_before=Pt(0);pf.space_after=Pt(0)
    pf.keep_together=True;pf.keep_with_next=False
    pf.left_indent=pf.right_indent=pf.first_line_indent=Cm(0)
    # A source Normal style may have an exact line height or first-line indent;
    # neither may clip/wrap the second face of this newly generated card pair.
    pf.line_spacing=1.0
    if len(cards)>1:
        pf.tab_stops.add_tab_stop(Cm(height*aspects[0]+gap))
    for index,(buffer,w,h) in enumerate(cards):
        buffer.seek(0)
        if index:para.add_run().add_tab()
        para.add_run().add_picture(buffer,width=Cm(height*aspects[index]),height=Cm(height))
    return len(cards)


def _compose_id_strip(buffers: list[io.BytesIO]) -> tuple[io.BytesIO, float, float] | None:
    from PIL import Image

    cards: list = []
    for buf in buffers:
        try:
            buf.seek(0)
            with Image.open(buf) as raw:
                cards.append(_normalize_id_image(raw, force_landscape=True))
        except Exception:
            logger.exception("open id scan for strip failed")
    if not cards:
        return None
    resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.BICUBIC)
    card_h = 220
    prepared = []
    for im in cards:
        im = _force_landscape(im)
        w, h = im.size
        nw = max(1, int(round(card_h * w / float(h or 1))))
        prepared.append(im.resize((nw, card_h), resample))
    gap = 16
    canvas_w = sum(im.size[0] for im in prepared) + gap * (len(prepared) - 1)
    canvas = Image.new("RGB", (max(canvas_w, 1), card_h), (255, 255, 255))
    x = 0
    for im in prepared:
        canvas.paste(im, (x, 0))
        x += im.size[0] + gap
    # 单张按标准身份证尺寸上限，两张并排；窄版心由调用方等比缩小
    width_cm = _ID_STRIP_WIDTH_CM if len(prepared) > 1 else _ID_SINGLE_WIDTH_CM
    width_cm, height_cm = _fit_cm(
        canvas.size[0], card_h, max_w=width_cm, max_h=_ID_STRIP_MAX_H_CM
    )
    px_w = max(1, int(round(width_cm / 2.54 * _ID_PREVIEW_DPI)))
    px_h = max(1, int(round(height_cm / 2.54 * _ID_PREVIEW_DPI)))
    canvas = canvas.resize((px_w, px_h), resample)
    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=82, dpi=(_ID_PREVIEW_DPI, _ID_PREVIEW_DPI), optimize=True)
    out.seek(0)
    return out, width_cm, height_cm


def _bake_id_jpeg(buf: io.BytesIO, *, width_cm: float, height_cm: float) -> io.BytesIO:
    """把图缩到与排版厘米一致的像素，docx-preview 即使用原图尺寸也不会撑满整页。"""
    from PIL import Image

    buf.seek(0)
    with Image.open(buf) as raw:
        image = raw.convert("RGB")
        px_w = max(1, int(round(width_cm / 2.54 * _ID_PREVIEW_DPI)))
        px_h = max(1, int(round(height_cm / 2.54 * _ID_PREVIEW_DPI)))
        resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.BICUBIC)
        image = image.resize((px_w, px_h), resample)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=82, dpi=(_ID_PREVIEW_DPI, _ID_PREVIEW_DPI), optimize=True)
        out.seek(0)
        return out


def _insert_image(doc: Document, path: Path, *, width_cm: float = 15.5, sharp: bool = False) -> bool:
    if not path.is_file():
        return False
    try:
        from PIL import Image

        with Image.open(path) as raw:
            image = raw.convert("RGB")
            if not sharp:
                image = _shrink_pil(image)
            buf = io.BytesIO()
            if sharp:
                image.save(buf, format="PNG")
            else:
                image.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=False)
            buf.seek(0)
            max_h = _CREDIT_MAX_H_CM if sharp else _IMAGE_MAX_H_CM
            disp_w, disp_h = _fit_to_page(doc, image.size[0], image.size[1], max_w=width_cm, max_h=max_h)
            pic = doc.add_paragraph()
            pic.alignment = WD_ALIGN_PARAGRAPH.CENTER
            pic.paragraph_format.space_before = Pt(4)
            pic.paragraph_format.space_after = Pt(4)
            pic.add_run().add_picture(buf, width=Cm(disp_w), height=Cm(disp_h))
        return True
    except Exception:
        pic = doc.add_paragraph()
        pic.alignment = WD_ALIGN_PARAGRAPH.CENTER
        pic.add_run().add_picture(str(path), width=Cm(width_cm))
        return True


def _fit_to_page(
    doc: Document,
    px_w: int,
    px_h: int,
    *,
    max_w: float,
    max_h: float,
) -> tuple[float, float]:
    remain = _page_body_cm(doc) - _used_on_page_cm(doc) - 0.4
    max_h = min(max_h, _scan_height_cm(doc))
    if remain < 4.0 and _used_on_page_cm(doc) > 1.0:
        doc.add_page_break()
        remain = _page_body_cm(doc) - 0.8
    cap = min(max_h, max(4.0, remain))
    return _fit_cm(px_w, px_h, max_w=max_w, max_h=cap)


def _scan_height_cm(doc: Document) -> float:
    """Reserve captions/gaps so normal scans can share a body page."""
    return min(_IMAGE_MAX_H_CM, max(4.0, _page_body_cm(doc) / 2 - 1.0))


def _render_pdf_page_bytes(page, *, sharp: bool) -> io.BytesIO:
    if sharp:
        scale = _CREDIT_DPI / 72.0
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil().convert("RGB")
        image = _shrink_pil(image, max_px=_CREDIT_MAX_PX)
        buf = io.BytesIO()
        image.save(buf, format="PNG", dpi=(_CREDIT_DPI, _CREDIT_DPI))
        buf.seek(0)
        return buf
    return _render_pdf_page_jpeg(page)


def _insert_pdf_pages(
    doc: Document,
    pdf_path: Path,
    *,
    max_pages: int = _SLOT_PDF_MAX_PAGES,
    budget: dict[str, int] | None = None,
    sharp: bool = False,
) -> int:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        logger.warning("pypdfium2 missing, skip slot pdf %s", pdf_path.name)
        return 0
    try:
        pdf = pdfium.PdfDocument(str(pdf_path))
    except Exception:
        logger.exception("open slot pdf failed: %s", pdf_path)
        return 0

    try:
        count = min(len(pdf), max_pages)
        inserted = 0
        max_h = _CREDIT_MAX_H_CM if sharp else _IMAGE_MAX_H_CM
        for i in range(count):
            if budget is not None and budget.get("left", 0) <= 0:
                break
            try:
                buf = _render_pdf_page_bytes(pdf[i], sharp=sharp)
                px_w, px_h = 210, 297
                try:
                    from PIL import Image

                    buf.seek(0)
                    with Image.open(buf) as preview:
                        px_w, px_h = preview.size
                    buf.seek(0)
                except Exception:
                    pass
                disp_w, disp_h = _fit_to_page(doc, px_w, px_h, max_w=15.5, max_h=max_h)
                pic = doc.add_paragraph()
                pic.alignment = WD_ALIGN_PARAGRAPH.CENTER
                pic.paragraph_format.space_before = Pt(2)
                pic.paragraph_format.space_after = Pt(2)
                buf.seek(0)
                pic.add_run().add_picture(buf, width=Cm(disp_w), height=Cm(disp_h))
                inserted += 1
                if budget is not None:
                    budget["left"] = max(0, int(budget["left"]) - 1)
            except Exception:
                logger.exception("render slot pdf page %s of %s failed", i + 1, pdf_path.name)
        return inserted
    finally:
        try:
            pdf.close()
        except Exception:
            pass


def _write_plain(cell: _Cell, text: str) -> None:
    cell.text = ""
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    para = cell.paragraphs[0]
    para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = para.add_run(text)
    _font(run, 9, bold=False)


def _font(run, size_pt: float | None = None, *, bold: bool | None = None) -> None:
    if bold is not None:
        run.bold = bold
    if size_pt is not None:
        run.font.size = Pt(size_pt)
    run.font.name = _SONG
    run.font.color.rgb = RGBColor(0, 0, 0)
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:ascii"), _SONG)
    rfonts.set(qn("w:hAnsi"), _SONG)
    rfonts.set(qn("w:eastAsia"), _SONG)


def _set_dashed_borders(table: Table) -> None:
    tbl_pr = table._tbl.tblPr
    if tbl_pr is None:
        tbl_pr = OxmlElement("w:tblPr")
        table._tbl.insert(0, tbl_pr)
    for old in tbl_pr.findall(qn("w:tblBorders")):
        tbl_pr.remove(old)
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "dashed")
        el.set(qn("w:sz"), "18")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), "7A7A7A")
        borders.append(el)
    tbl_pr.append(borders)


def _set_row_height(row, height_cm: float, *, exact: bool = False) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    for old in tr_pr.findall(qn("w:trHeight")):
        tr_pr.remove(old)
    el = OxmlElement("w:trHeight")
    el.set(qn("w:val"), str(int(Cm(height_cm).twips)))
    el.set(qn("w:hRule"), "exact" if exact else "atLeast")
    tr_pr.append(el)


def _set_cell_margins(cell: _Cell, *, top: int, bottom: int, left: int, right: int) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    for old in tc_pr.findall(qn("w:tcMar")):
        tc_pr.remove(old)
    mar = OxmlElement("w:tcMar")
    for name, val in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
        node = OxmlElement(f"w:{name}")
        node.set(qn("w:w"), str(val))
        node.set(qn("w:type"), "dxa")
        mar.append(node)
    tc_pr.append(mar)
