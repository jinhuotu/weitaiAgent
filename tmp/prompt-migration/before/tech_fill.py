"""技术标步骤：工程量清单和图纸 PDF 抽字后起草。不写报价行。"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from common.errors import AppError, ErrorCode

logger = logging.getLogger("api.tenders.tech_fill")

_BOQ_EXT = {".xlsx", ".xlsm", ".docx", ".csv", ".pdf"}
_MAX_BYTES = 40 * 1024 * 1024

_BOQ_SYSTEM = """你是技术标施工组织起草。根据给出的工程量清单项目写一章正文。
硬性规则：
1. 项目名称、单位、数量只可照录清单。清单里没有的数字写【待补充】，禁止编造。
2. 不要写单价、合价、投标报价、税率。
3. 不要写招标人、项目名称、公司全称。
4. 只输出正文，不要标题，不要 Markdown。"""

_DRAW_SYSTEM = """你是技术标图纸说明起草。根据图纸 PDF 抽出的文字写一段说明。
硬性规则：
1. 只写文字里有的图名、系统、尺寸、材料。没有的写【待补充】，禁止编造工程量。
2. 不要写单价和投标报价。
3. 只输出正文，不要标题，不要 Markdown。"""


def _clip(text: str, n: int = 6000) -> str:
    return (text or "").strip()[:n]


def sheet_qty_text(sheet) -> str:
    rows: list[str] = []
    for line in getattr(sheet, "lines", ()) or ():
        name = (getattr(line, "name", "") or "").strip()
        if not name:
            continue
        bits = [name]
        spec = (getattr(line, "spec", "") or "").strip()
        unit = (getattr(line, "unit", "") or "").strip()
        qty = getattr(line, "qty", 0) or 0
        if spec:
            bits.append(spec)
        if unit:
            bits.append(unit)
        if qty:
            bits.append(str(qty))
        rows.append(" ".join(bits))
        if len(rows) >= 60:
            break
    return "\n".join(rows)


def _plain(title: str, facts: str) -> str:
    return f"{title}\n{facts}".strip()


async def _read_upload(data: bytes, filename: str, allowed: set[str]) -> tuple[str, object | None]:
    if not data:
        raise AppError(ErrorCode.VALIDATION, "上传文件为空", status_code=422)
    if len(data) > _MAX_BYTES:
        raise AppError(ErrorCode.VALIDATION, "单个文件不能超过 40MB", status_code=422)
    suffix = Path(filename or "").suffix.lower()
    if suffix not in allowed:
        if allowed == _BOQ_EXT:
            raise AppError(ErrorCode.VALIDATION, "工程量清单支持 Excel、Word、CSV 或 PDF", status_code=422)
        raise AppError(ErrorCode.VALIDATION, "图纸只支持 PDF", status_code=422)
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    path = Path(tmp.name)
    try:
        tmp.write(data)
        tmp.close()
        sheet = None
        if suffix in {".xlsx", ".xlsm", ".csv", ".docx"}:
            from api.services.tenders.quote import parse_quote_path

            try:
                sheet = parse_quote_path(path)
            except Exception:
                logger.exception("工程量清单表格解析失败")
                sheet = None
        from api.services.knowledge.parsers import extract_text_from_file

        try:
            extracted = await extract_text_from_file(path, ext=suffix.lstrip("."))
            raw = _clip(getattr(extracted, "text", "") or "")
        except Exception:
            logger.exception("技术标附件抽字失败")
            raw = ""
        if sheet is None and raw:
            from api.services.tenders.quote import parse_quote_from_text

            try:
                sheet = parse_quote_from_text(raw, title_hint="工程量清单")
            except Exception:
                sheet = None
        return raw, sheet
    finally:
        path.unlink(missing_ok=True)


async def _complete(client, system: str, user: str) -> str:
    if client is None:
        return ""
    try:
        raw = await client.complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            mode="fast",
        )
    except Exception as exc:
        logger.warning("tech fill llm skipped: %s", exc)
        return ""
    return (raw or "").strip()


async def fill_boq(data: bytes, filename: str, *, client=None) -> str:
    raw, sheet = await _read_upload(data, filename, _BOQ_EXT)
    facts = sheet_qty_text(sheet) if sheet is not None else ""
    if not facts:
        facts = _clip(raw, 4000)
    if not facts:
        return "【待补充】工程量清单没有识别出项目。"
    text = await _complete(
        client,
        _BOQ_SYSTEM,
        "工程量清单项目（无单价）：\n" + facts,
    )
    if text:
        return text
    return _plain("根据工程量清单整理（数量照录，不含报价）：", facts)


async def fill_drawing(data: bytes, filename: str, *, client=None) -> str:
    raw, _sheet = await _read_upload(data, filename, {".pdf"})
    facts = _clip(raw, 4000)
    if not facts:
        return "【待补充】图纸 PDF 未识别出文字，页图仍会贴进技术标。"
    text = await _complete(client, _DRAW_SYSTEM, "图纸抽出的文字：\n" + facts)
    if text:
        return text
    return _plain("图纸文字摘录：", facts)
