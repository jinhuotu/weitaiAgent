"""企业资质 PDF 查找。不入库知识库，生成时按页插入 Word。"""

from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

from common.config import get_settings

_INVITE_ID_RE = re.compile(r"^[a-f0-9]{10,16}$", re.I)
_INVITE_TEXT_MAX = 80_000

_CHAPTER5_NAME = "chapter5.docx"
_QUOTE_NAME = "quote.xlsx"


def tender_assets_dir() -> Path:
    root = Path(get_settings().storage_root).expanduser().resolve()
    path = root / "tender-assets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def tenders_output_dir() -> Path:
    root = Path(get_settings().storage_root).expanduser().resolve()
    path = root / "tenders"
    path.mkdir(parents=True, exist_ok=True)
    return path


def tender_invitations_dir() -> Path:
    root = Path(get_settings().storage_root).expanduser().resolve()
    path = root / "tender-invitations"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_invitation_text(text: str, *, stem: str = "") -> str:
    """把邀请书抽出的正文落到 tender-invitations/{id}.txt，供生成后质检对照。"""
    raw_stem = (stem or "").strip()
    invite_id = raw_stem if _INVITE_ID_RE.fullmatch(raw_stem) else uuid4().hex[:12]
    dest = tender_invitations_dir() / f"{invite_id}.txt"
    dest.write_text((text or "").strip(), encoding="utf-8")
    return invite_id


def load_invitation_text(invitation_id: str) -> str:
    invite_id = (invitation_id or "").strip()
    if not _INVITE_ID_RE.fullmatch(invite_id):
        return ""
    path = tender_invitations_dir() / f"{invite_id}.txt"
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def save_material_source_text(invitation_id: str, text: str) -> None:
    """Keep full material-recognition input; prompt/QA limits must not truncate it."""
    if not _INVITE_ID_RE.fullmatch(invitation_id or ''):
        raise ValueError('invalid invitation id')
    (tender_invitations_dir() / f'{invitation_id}.materials.txt').write_text(text or '',encoding='utf-8')


def load_material_source_text(invitation_id: str) -> str:
    if not _INVITE_ID_RE.fullmatch(invitation_id or ''):
        return ''
    path=tender_invitations_dir() / f'{invitation_id}.materials.txt'
    try:
        return path.read_text(encoding='utf-8') if path.is_file() else load_invitation_text(invitation_id)
    except OSError:
        return load_invitation_text(invitation_id)


def find_chapter5_template() -> Path | None:
    """公司固定投标文件空白稿：优先用 storage 里更换过的文件，否则用仓库内置 chapter5.docx。"""
    stored = tender_assets_dir() / _CHAPTER5_NAME
    if stored.is_file() and stored.stat().st_size > 1000:
        return stored
    bundled = Path(__file__).resolve().parent / "templates" / _CHAPTER5_NAME
    if bundled.is_file() and bundled.stat().st_size > 1000:
        return bundled
    return None


def bundled_chapter5_template() -> Path | None:
    bundled = Path(__file__).resolve().parent / "templates" / _CHAPTER5_NAME
    if bundled.is_file() and bundled.stat().st_size > 1000:
        return bundled
    return None


def chapter5_template_status() -> dict[str, object]:
    stored = tender_assets_dir() / _CHAPTER5_NAME
    custom = stored.is_file() and stored.stat().st_size > 1000
    path = find_chapter5_template()
    return {
        "source": "custom" if custom else ("bundled" if path is not None else "missing"),
        "custom": custom,
        "found": path is not None,
        "sizeBytes": path.stat().st_size if path is not None else 0,
        "label": "已更换的公司模板" if custom else "内置投标文件格式",
    }


def save_chapter5_template(data: bytes) -> dict[str, object]:
    from common.errors import AppError, ErrorCode

    raw = data or b""
    max_bytes = int(get_settings().kb_upload_max_bytes)
    if len(raw) > max_bytes:
        raise AppError(ErrorCode.VALIDATION, f"模板超过大小上限（{max_bytes} 字节）", status_code=422)
    if len(raw) < 1000 or not raw.startswith(b"PK"):
        raise AppError(ErrorCode.VALIDATION, "请上传 Word 空白稿（.docx）", status_code=422)
    dest = tender_assets_dir() / _CHAPTER5_NAME
    dest.write_bytes(raw)
    return chapter5_template_status()


def restore_chapter5_template() -> dict[str, object]:
    stored = tender_assets_dir() / _CHAPTER5_NAME
    if stored.is_file():
        stored.unlink()
    return chapter5_template_status()


def find_quote_xlsx() -> Path | None:
    """招标工程量清单。仅仓库副本或 storage/tender-assets。"""
    bundled = Path(__file__).resolve().parent / "templates" / _QUOTE_NAME
    if bundled.is_file() and bundled.stat().st_size > 1000:
        return bundled
    stored = tender_assets_dir() / _QUOTE_NAME
    if stored.is_file() and stored.stat().st_size > 1000:
        return stored
    return None


def find_qualification_pdf() -> Path | None:
    """发现资质附件；生成时另行校验所属投标人，不按品牌优先选择。"""
    folder = tender_assets_dir()
    candidates = [
        path
        for path in folder.rglob("*.pdf")
        if path.is_file() and path.stat().st_size > 1000
        and ("资质" in path.stem or "qualification" in path.stem.casefold())
    ]
    if candidates:
        return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]
    return None


def qualification_matches_company(path: Path, bidder_name: str) -> bool:
    """A global attachment requires positive identity evidence before adoption."""
    from api.services.tenders.company_identity import company_key, foreign_bidder

    if not company_key(bidder_name):
        return False
    try:
        import fitz

        with fitz.open(path) as pdf:
            text = '\n'.join(pdf[i].get_text() for i in range(min(5, len(pdf))))
    except Exception:
        return False
    return (company_key(bidder_name) in company_key(path.stem + '\n' + text)
            and not foreign_bidder(text, bidder_name))
