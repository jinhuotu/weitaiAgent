"""Format previews: native excerpts remain editable; generated skeletons are isolated."""
from __future__ import annotations

import re
import shutil
from uuid import uuid4

from api.services.tenders.assets import (
    find_chapter5_template,
    tender_invitations_dir,
    tenders_output_dir,
)
from api.services.tenders.pdf_format import _ready_format_docx
from api.services.tenders.schema import BidBrief
from common.errors import AppError, ErrorCode

_FALLBACK_NOTE = '未抽到格式章，预览为内置模板骨架，仅展示格式，正文待生成'


def prepare_invitation_format_docx(
    invitation_id: str, *, brief: BidBrief | None = None,
) -> dict:
    invite_id = (invitation_id or '').strip()
    if not re.fullmatch(r'[a-f0-9]{10,16}', invite_id):
        raise AppError(ErrorCode.BAD_REQUEST, '无效的招标文件标识', status_code=400)
    src = _ready_format_docx(invite_id)
    if src is None and not any((tender_invitations_dir() / f'{invite_id}.{ext}').is_file()
                               for ext in ('docx', 'txt')):
        raise AppError(ErrorCode.NOT_FOUND, '招标文件不存在，请重新上传并识别', status_code=404)

    prepared = brief.model_copy(deep=True) if brief is not None else None
    if prepared is not None:
        from api.services.tenders.requirement_bid import prepare_requirements_bid

        prepared.invitationId = invite_id
        prepare_requirements_bid(prepared)
    fixed = prepared is not None and prepared.layoutMode == 'chapter5'
    requirements = prepared is not None and prepared.requirementsDriven
    if src is not None and not fixed and not requirements:
        dest = tenders_output_dir() / f'{invite_id}.format.docx'
        shutil.copyfile(src, dest)
        return {'fileName': dest.name, 'source': 'invitation', 'previewOnly': False, 'message': ''}

    # A skeleton must never be named *.format.docx: OnlyOffice callbacks sync
    # those names into the native-format source used for later generation.
    dest = tenders_output_dir() / f'{uuid4().hex[:12]}.docx'
    if requirements:
        from api.services.tenders.document import build_bid_format_skeleton

        build_bid_format_skeleton(prepared, dest)
        source = 'requirements'
        note = _FALLBACK_NOTE + '；章节按本标综合大纲排列'
    else:
        template = find_chapter5_template()
        if template is None:
            raise AppError(ErrorCode.NOT_FOUND, '未找到当前生成使用的内置模板', status_code=404)
        shutil.copyfile(template, dest)
        source = 'builtin'
        note = (_FALLBACK_NOTE if src is None else
                '预览为当前选用的内置模板骨架，仅展示格式，正文待生成')
    return {'fileName': dest.name, 'source': source, 'previewOnly': True, 'message': note}
