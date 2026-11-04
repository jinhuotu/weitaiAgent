"""原始 Word 章节目录与只读切片，独立于脱敏检索索引。"""

from io import BytesIO
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from api.services.tenders.bid_archive import _files_of, chapter_ranges
from common.errors import AppError, ErrorCode


def source_files(row) -> list[tuple[str, Path]]:
    from api.services.tenders.assets import tenders_output_dir

    root = tenders_output_dir()
    source = (row.brief_json or {}).get("_historySource")
    pairs = [("", source)] if source else _files_of(row, None)
    return [
        (vol, root / name)
        for vol, name in pairs
        if name and Path(name).name == name and (root / name).is_file()
    ]


def source_chapters(row) -> list[dict]:
    from api.services.tenders.categories import slot_volume

    out = []
    for volume, path in source_files(row):
        for title, start, _end in chapter_ranges(Document(str(path))):
            out.append(
                {
                    "volume": volume or slot_volume("", title),
                    "title": title[:40],
                    "displayTitle": title,
                    "docId": "",
                    "previewKey": str(start),
                }
            )
    return out


async def preview_chapter(db, record_id: str, title: str, volume: str, key: str, *, user, admin):
    from api.services.tenders.records import _assert_owner_or_admin, _get_row

    row = await _get_row(db, record_id)
    _assert_owner_or_admin(row, user, admin=admin)
    from api.services.tenders.categories import slot_volume

    for vol, path in source_files(row):
        doc = Document(str(path))
        for heading, start, end in chapter_ranges(doc):
            if (
                volume
                and (vol or slot_volume("", heading)) != volume
                or key
                and key != str(start)
                or not key
                and heading != title
                and heading[:40] != title
            ):
                continue
            kids = list(doc.element.body)
            # 原包上删减正文，保留样式、编号、图片、关系及所属章节页面设置。
            section = None
            for el in kids[start:]:
                nested = el if el.tag == qn("w:sectPr") else el.find(".//" + qn("w:sectPr"))
                if nested is not None:
                    section = nested
                    break
            if section is not None:
                from copy import deepcopy

                section = deepcopy(section)
            for i, el in enumerate(kids):
                if not start <= i < end or el.tag == qn("w:sectPr"):
                    doc.element.body.remove(el)
            if section is not None:
                doc.element.body.append(section)
            settings = doc.settings.element
            for old in list(settings.findall(qn("w:documentProtection"))):
                settings.remove(old)
            protection = OxmlElement("w:documentProtection")
            protection.set(qn("w:edit"), "readOnly")
            protection.set(qn("w:enforcement"), "1")
            settings.append(protection)
            data = BytesIO()
            doc.save(data)
            return data.getvalue()
    raise AppError(
        ErrorCode.NOT_FOUND,
        "原始 Word 或章节不存在，请重新上传历史标书",
        status_code=404,
    )
