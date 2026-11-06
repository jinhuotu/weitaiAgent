"""Insert this upload's invitation only at an explicit submission-format site."""
from __future__ import annotations

import json
import re
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

from api.services.tenders.assets import tender_invitations_dir

_INVITE = r'(?:投标|招标)邀请(?:书|函)'
_PREFIX = r'(?:(?:第[一二三四五六七八九十\d]+[章节])|(?:[（(]?[一二三四五六七八九十\d]+[)）、.．]))?'
_TITLE = re.compile(r'^' + _PREFIX + _INVITE + r'(?:[（(](?:复印件|扫描件|原件)[）)])?$')
_NEGATIVE = re.compile(r'无需|不需|不必|免于|不要求|不得|不用')


def _text(node):
    return ''.join(t.text or '' for t in node.iter(qn('w:t'))).strip()


def _site(node):
    text = re.sub(r'\s+', '', _text(node))
    if _NEGATIVE.search(text) or 'PAGEREF' in node.xml:
        return False
    style = node.find('w:pPr/w:pStyle', node.nsmap)
    if style is not None and (style.get(qn('w:val')) or '').lower().startswith('toc'):
        return False
    if next(node.iter(qn('w:hyperlink')), None) is not None:
        return False
    return bool(_TITLE.fullmatch(text) or (
        len(text) < 180 and re.search(_INVITE, text)
        and re.search(r'(?:此处|在此|本节|本页|本表后|后附|应附|须附|需附|粘贴|附上)', text)
    ))


def save_sources(invitation_id: str, paths, names, texts):
    if not re.fullmatch(r'[a-f0-9]{10,16}', invitation_id or '', re.I):
        return
    root = tender_invitations_dir().resolve()
    rows = []
    for path, name, text in zip(paths, names, texts):
        path = Path(path).resolve()
        if path.parent != root or path.suffix.lower() != '.docx':
            continue
        # A tender book containing a format chapter is never a standalone letter.
        format_book = bool(re.search(r'(?:第[一二三四五六七八九十\d]+[章节]\s*)?(?:投标|响应)文件格式', text))
        letter = not format_book and bool(re.search(_INVITE, str(name)) or re.search(_INVITE, text[:160]))
        rows.append({'file': path.name, 'name': str(name), 'letter': letter})
    (root / f'{invitation_id}.sources.json').write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')


def _source_rows(invitation_id):
    if not re.fullmatch(r'[a-f0-9]{10,16}', invitation_id or '', re.I):
        return []
    root = tender_invitations_dir().resolve()
    try:
        rows = json.loads((root / f'{invitation_id}.sources.json').read_text(encoding='utf-8'))
        return [r for r in rows if isinstance(r, dict) and Path(r.get('file', '')).name == r.get('file')
                and (root / r['file']).is_file() and Path(r['file']).suffix.lower() == '.docx']
    except (OSError, ValueError, TypeError):
        return []


def _required(rows, root):
    from api.services.tenders.native_format import word_format_section
    for row in rows:
        if row.get('letter'):
            continue
        _, section = word_format_section(root / row['file'])
        if not section:
            continue
        # An explicit exemption wins over a retained blank heading/template.
        if any(_NEGATIVE.search(line) and re.search(_INVITE, line) for line in section.splitlines()):
            continue
        for line in section.splitlines():
            text = re.sub(r'\s+', '', line)
            if _NEGATIVE.search(text):
                continue
            if _TITLE.fullmatch(text) or (re.search(_INVITE, text) and re.search(r'应附|须附|需附|粘贴|附上|文件包括|文件组成|应包括', text)):
                return True
    return False


def format_source(paths):
    """Choose the format-bearing upload, independently of the upload order."""
    from api.services.tenders.native_format import word_format_section
    for path in paths:
        try:
            if word_format_section(Path(path))[1]:
                return Path(path)
        except Exception:
            continue
    return Path(paths[0])


def insert_required_invitation(dest: Path, invitation_id: str):
    """Return warnings; leave files byte-identical when there is no requirement."""
    rows = _source_rows(invitation_id)
    root = tender_invitations_dir()
    if not rows:
        doc = Document(dest)
        return (['当前格式有邀请书位置，但旧任务没有保存上传原件关联；请重新上传本次招标资料后生成。']
                if any(_site(n) for n in doc.element.body) else [])
    try:
        required = _required(rows, root)
    except Exception:
        return ['无法读取本次上传的格式要求，邀请书未自动插入；请检查原件。']
    if not required:
        return []
    doc = Document(dest)
    if any(b.get(qn('w:name')) == 'uploaded_invitation_original' for b in doc.element.body.iter(qn('w:bookmarkStart'))):
        return []
    from api.services.tenders.directory_guard import directory_blocks
    protected = directory_blocks(doc)
    anchors = [n for n in doc.element.body if n not in protected and _site(n)]
    if not anchors:
        return ['格式要求附投标邀请书，但当前卷未找到指定位置，未擅自追加；请核对对应卷。']
    # A composition list can repeat the title before the real attachment page.
    anchor = anchors[-1]
    candidates = [root / r['file'] for r in rows if r.get('letter')]
    if len(candidates) != 1:
        return ['格式要求附投标邀请书，但本次上传的邀请书原件缺失或存在多个候选；请明确上传对应邀请书。']
    try:
        from api.services.tenders.preview_pdf import ensure_preview_pdf
        from api.services.tenders.placeholders import _drop_after
        from api.services.tenders.schema import PlaceholderItem
        # Render the original, rather than recreating its contents or stamps.
        pdf = ensure_preview_pdf(candidates[0])
        slot = PlaceholderItem(key='uploaded_invitation_original', title='投标邀请书原件')
        if not _drop_after(doc, anchor, slot, [pdf], complete=True, at_marker=True):
            return ['邀请书原件插入失败，请检查文件或转换服务。']
        from docx.oxml import OxmlElement
        marker = OxmlElement('w:bookmarkStart')
        marker.set(qn('w:name'), 'uploaded_invitation_original')
        bookmark_id = max((int(b.get(qn('w:id'), '0')) for b in doc.element.body.iter(qn('w:bookmarkStart'))), default=0) + 1
        marker.set(qn('w:id'), str(bookmark_id))
        end = OxmlElement('w:bookmarkEnd')
        end.set(qn('w:id'), str(bookmark_id))
        para = next(anchor.iter(qn('w:p')), anchor)
        para.append(marker)
        para.append(end)
        doc.save(dest)
        return []
    except Exception:
        import logging
        logging.getLogger(__name__).exception('invitation attachment failed')
        return ['邀请书原件转换或插入失败，请检查原件；未将其他上传文件作为邀请书。']
