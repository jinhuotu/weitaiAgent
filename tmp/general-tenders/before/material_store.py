"""Opt-in material archives. No renderer or legacy library writes happen here."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4

from api.services.tenders.assets import tender_assets_dir
from common.errors import AppError, ErrorCode

CATEGORIES = {
    'addendum': ('补遗文件', 'project', ('补遗',)),
    'clarification': ('答疑/澄清文件', 'project', ('答疑', '澄清')),
    'correction': ('勘误文件', 'project', ('勘误',)),
    'survey': ('现场踏勘资料', 'project', ('踏勘',)),
    'geology': ('地勘资料', 'project', ('地勘',)),
    'site_plan': ('总平面图', 'project', ('总平面图',)),
    'system_diagram': ('系统图', 'project', ('系统图',)),
    'construction_drawing': ('施工图', 'project', ('施工图',)),
    'detail_drawing': ('节点详图', 'project', ('节点详图',)),
    'personnel': ('人员库', 'library', ('简历', '注册证书', '职称证书', '岗位证书', '社保')),
    'product': ('产品/设备库', 'library', ('产品参数', '设备清单', '检测报告')),
    'performance': ('业绩库', 'library', ('合同', '中标通知书', '验收报告')),
}
SCAN_EXT = {'.pdf', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'}
ALLOWED_EXT = SCAN_EXT | {'.doc', '.docx', '.xlsx', '.xls', '.txt', '.dwg', '.dxf'}
MAX_BYTES = 40 * 1024 * 1024


def invalid(message):
    raise AppError(ErrorCode.VALIDATION, message, status_code=422)


def catalog():
    return [dict(key=k, title=v[0], scope=v[1], subtypes=list(v[2]), enabled=False)
            for k, v in CATEGORIES.items()]


def _scope(owner: int, category: str, invitation: str):
    if category not in CATEGORIES or int(owner) <= 0:
        invalid('无效资料分类')
    scope = 'library'
    if CATEGORIES[category][1] == 'project':
        if not re.fullmatch(r'[a-f0-9]{10,16}', invitation or ''):
            invalid('请先识别本项目邀请书')
        scope = invitation
    return tender_assets_dir() / 'material-archives' / str(int(owner)) / scope / category


def list_materials(owner: int, invitation: str = ''):
    items = []
    for category in CATEGORIES:
        if CATEGORIES[category][1] == 'project' and not invitation:
            continue
        folder = _scope(owner, category, invitation)
        for path in sorted(folder.glob('*.json')):
            item = json.loads(path.read_text(encoding='utf-8'))
            items.append(item)
    return {'categories': catalog(), 'files': items}


def material_path(owner, invitation, category, file_id):
    if not re.fullmatch(r'[a-f0-9]{32}', file_id or ''):
        invalid('无效资料编号')
    folder = _scope(owner, category, invitation)
    metadata = folder / f'{file_id}.json'
    if not metadata.is_file():
        raise AppError(ErrorCode.NOT_FOUND, '资料不存在或不属于当前用户/项目', status_code=404)
    item = json.loads(metadata.read_text(encoding='utf-8'))
    path = folder / (file_id + '.source' + item['extension'])
    if not path.is_file():
        invalid('归档原文件缺失，请重新上传')
    return path, item


async def upload_material(owner, invitation, category, *, filename, data, entity='', subtype=''):
    folder = _scope(owner, category, invitation)
    name = Path((filename or '').replace('\\', '/')).name[:240]
    ext = Path(name).suffix.lower()
    if not data or len(data) > MAX_BYTES or ext not in ALLOWED_EXT:
        invalid('请上传非空、40MB以内的 Word/Excel/PDF/图片/TXT/CAD 文件')
    if subtype not in CATEGORIES[category][2]:
        invalid('请选择该分类下的资料类型')
    entity = entity.strip()[:120]
    if CATEGORIES[category][1] == 'library' and not entity:
        invalid('请填写人员姓名、产品设备名称或业绩项目名称')
    if len(list(folder.glob('*.json'))) >= 200:
        invalid('该分类已达200个文件，请先整理归档')
    folder.mkdir(parents=True, exist_ok=True)
    file_id = uuid4().hex
    path = folder / (file_id + '.source' + ext)
    path.write_bytes(data)
    status, note, text = 'archive_only', 'CAD原文件仅归档，请另传PDF用于预览和组卷', ''
    if ext not in {'.dwg', '.dxf'}:
        try:
            from api.services.knowledge.parsers import extract_text_from_file
            parsed = await asyncio.wait_for(extract_text_from_file(path, ext=ext[1:]), timeout=45)
            text = (parsed.text or '').strip()[:60000]
            status = 'parsed' if text else 'no_text'
            note = '文字已解析，默认仅入库' if text else '已归档，未提取到文字；PDF/图片仍可作为扫描件使用'
        except Exception:
            status, note = 'parse_failed', '原文件已归档，文字解析失败；可重新上传可读取版本'
    (folder / f'{file_id}.txt').write_text(text, encoding='utf-8')
    item = dict(id=file_id, category=category, filename=name, extension=ext,
                entity=entity, subtype=subtype, sizeBytes=len(data), chars=len(text),
                parseStatus=status, note=note, enabled=False,
                sha256=hashlib.sha256(data).hexdigest())
    metadata = folder / f'{file_id}.json.tmp'
    metadata.write_text(json.dumps(item, ensure_ascii=False), encoding='utf-8')
    metadata.replace(folder / f'{file_id}.json')
    return item


def delete_material(owner, invitation, category, file_id):
    path, _ = material_path(owner, invitation, category, file_id)
    path.unlink()
    (path.parent / f'{file_id}.txt').unlink(missing_ok=True)
    (path.parent / f'{file_id}.json').unlink(missing_ok=True)
    return {'deleted': True}


def selected_materials(owner, brief):
    """Resolve only explicit IDs under their authenticated owner's scope."""
    for category, choice in brief.materialSelections.items():
        if not choice.enabled:
            continue
        if category not in CATEGORIES:
            invalid('未知资料开关')
        for file_id in dict.fromkeys(choice.fileIds):
            path, item = material_path(owner, brief.invitationId, category, file_id)
            text_path = path.parent / f'{file_id}.txt'
            text = text_path.read_text(encoding='utf-8') if text_path.is_file() else ''
            yield choice, path, item, text


def existing_sections(brief):
    """Read available anchors without adding or changing any outline items."""
    from docx import Document
    from api.services.tenders.assets import find_chapter5_template
    from api.services.tenders.pdf_format import format_docx_path, toc_block_ids
    from api.services.tenders.native_format import _physical_chapter, _text
    titles = [i.title for i in brief.outlineItems if not i.skipped]
    # Do not call _word_format_file: that helper can regenerate a format cache.
    source = format_docx_path(brief.invitationId) if brief.invitationId else None
    source = source if source is not None and source.is_file() else None
    source = source or find_chapter5_template()
    if source is not None:
        doc = Document(source)
        toc = set(toc_block_ids(doc))
        titles.extend(_text(p._p) for p in doc.paragraphs
                      if p._p not in toc and _physical_chapter(p._p, doc))
    return list(dict.fromkeys(titles))


def scan_usable(path):
    try:
        if path.suffix == '.pdf':
            import fitz
            with fitz.open(path) as doc:
                return not doc.needs_pass and doc.page_count > 0
        from PIL import Image
        with Image.open(path) as image:
            image.verify()
        return True
    except Exception:
        return False


def preflight(owner, brief):
    """Read-only guidance; never creates outline entries or document placeholders."""
    available = list_materials(owner, brief.invitationId)
    warnings, selected = [], []
    section_titles = existing_sections(brief)
    titles = set(section_titles)
    for choice, path, item, text in selected_materials(owner, brief):
        mode = choice.mode
        messages = []
        if mode == 'scan' and path.suffix not in SCAN_EXT:
            messages.append('该文件不能直接贴图，请另传PDF/图片或改为起草参考')
        elif mode == 'scan' and not scan_usable(path):
            messages.append('扫描件无法读取，本次不贴图，请上传有效PDF/图片')
        if mode == 'scan' and choice.targetSection not in titles:
            messages.append('未选择现有对应章节，本次不贴图，不自动创建章节')
        if mode == 'reference' and not text:
            messages.append('无解析文字，不能参与起草参考')
        elif mode == 'reference':
            from api.services.tenders.source_match import match_source, require_source_match
            matched = require_source_match(match_source(brief, title=item['entity'], text=text,
                                                         source_name=item['filename']))
            if matched.status == 'unmatched':
                messages.append('资料与本项目不匹配或关联不足，本次不采用，请核对来源')
        if mode == 'reference' and not brief.autoDraftTechPlan:
            messages.append('技术方案自动起草未开启，本次参考资料不会送入模型')
        if mode == 'reference' and not (brief.generateBoth or brief.generateVolume in {'technical', 'full'}):
            messages.append('本次只生成商务标，参考资料不参与技术起草')
        selected.append(dict(id=item['id'], category=item['category'], filename=item['filename'],
                             mode=mode, targetSection=choice.targetSection, issues=messages))
        warnings.extend(f"{item['filename']}：{message}" for message in messages)
    for category, choice in brief.materialSelections.items():
        if choice.enabled and not choice.fileIds:
            warnings.append(f'{CATEGORIES.get(category, (category,))[0]}已开启，但未选择资料')
    # Completeness is per entity, never mix three unrelated projects/persons.
    groups = {}
    for item in available['files']:
        if item['category'] in {'personnel', 'product', 'performance'}:
            groups.setdefault((item['category'], item['entity']), set()).add(item['subtype'])
    missing = [dict(category=k, entity=e, missing=[s for s in CATEGORIES[k][2] if s not in have])
               for (k, e), have in groups.items() if set(CATEGORIES[k][2]) - have]
    return dict(selected=selected, warnings=warnings, missing=missing, sections=sorted(titles),
                note='检查清单仅供生成前核对，不写入正文或目录')


def generation_inputs(owner, brief, slots, media):
    """Adapt opt-in scans/references using existing generator arguments only."""
    from api.services.tenders.schema import PlaceholderItem
    from api.services.tenders.native_format import _key
    from api.services.tenders.source_match import match_source, require_source_match

    if not any(c.enabled for c in brief.materialSelections.values()):
        return slots, media, [], []
    result_slots, result_media, notes = list(slots), dict(media), []
    reference_ids = []
    section_titles = existing_sections(brief)
    titles = set(section_titles)
    grouped = {}
    for choice, path, item, text in selected_materials(owner, brief):
        if choice.mode == 'reference':
            if not text or not brief.autoDraftTechPlan or not (
                brief.generateBoth or brief.generateVolume in {'technical', 'full'}
            ):
                notes.append(f"{item['filename']}：仅归档，未用于技术起草")
                continue
            match = require_source_match(match_source(brief, title=item['entity'], text=text,
                                                      source_name=item['filename']))
            if match.status == 'unmatched':
                notes.append(f"{item['filename']}：与本项目不匹配，未采用")
                continue
            from api.services.tenders.gen_tech_plan import _source_dir
            source_id = item['id'][:16]
            folder = _source_dir()
            (folder / f'{source_id}.txt').write_text(text, encoding='utf-8')
            (folder / f'{source_id}.meta.json').write_text(
                json.dumps({'filename': item['filename']}, ensure_ascii=False), encoding='utf-8')
            if source_id not in brief.techPlanSourceIds:
                if len(brief.techPlanSourceIds) + len(reference_ids) >= 5:
                    notes.append(f"{item['filename']}：起草参考已达5份上限，未采用")
                    continue
                reference_ids.append(source_id)
            notes.append(f"{item['filename']}：已作为技术起草参考（不会直接复制条款）")
            continue
        target = choice.targetSection
        if path.suffix not in SCAN_EXT or target not in titles or not scan_usable(path):
            notes.append(f"{item['filename']}：缺少可贴图文件或现有对应章节，仅归档")
            continue
        from api.services.tenders.categories import item_volume
        section = next((i for i in brief.outlineItems if i.title == target), None)
        target_volume = item_volume(title=target, kind=section.kind if section else '')
        if (not brief.generateBoth and brief.generateVolume in {'business', 'technical'}
                and target_volume != brief.generateVolume):
            notes.append(f"{item['filename']}：对应章节不在本卷，仅归档")
            continue
        # Use a distinct catalog key per category + target, never a legacy key
        # that could distribute a person's scans into unrelated company slots.
        key = 'archive_' + hashlib.sha256((item['category'] + _key(target)).encode()).hexdigest()[:16]
        # Word automatic numbering appears in a TOC label but not p.text. Pass
        # the original physical heading text to the existing attachment API.
        anchor_target = next((t for t in reversed(section_titles) if _key(t) == _key(target)), target)
        grouped.setdefault(key, (anchor_target, [], target_volume))[1].append(path)
        notes.append(f"{item['filename']}：已选用，仅贴入「{target}」章节")
    for key, (target, files, target_volume) in grouped.items():
        # A synthetic unique catalog label avoids the renderer's substring
        # aliases distributing scans into other chapters with similar names.
        label = ('技术方案' if target_volume == 'technical' else '商务') + '新资料附件#' + key[8:]
        result_slots.append(PlaceholderItem(key=key, title=label, attachToSection=target))
        result_media[key] = files
    return result_slots, result_media, notes, reference_ids
