"""PDF 按版面转成可编辑 Word。本机有 Filee 时走它的 PDF→DOCX，否则用 pdf2docx。"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger("api.tenders.pdf_to_docx")


def convert_pdf_to_docx(src: Path, dest: Path) -> str:
    if not src.is_file() or src.suffix.lower() != ".pdf":
        raise RuntimeError("不是 PDF，无法按版面转 Word")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _via_filee(src, dest):
        return "已用 Filee 把 PDF 按版面转成 Word"
    _via_pdf2docx(src, dest)
    return "已按版面把 PDF 转成 Word"


def _filee_bin() -> str | None:
    # 便携包里图形程序也叫 Filee.exe，命令行是旁边的 filee-cli.exe
    cli = shutil.which("filee-cli")
    if cli:
        return cli
    exe = shutil.which("filee")
    if not exe:
        return None
    side = Path(exe).with_name("filee-cli.exe")
    return str(side) if side.is_file() else exe


def _via_filee(src: Path, dest: Path) -> bool:
    exe = _filee_bin()
    if not exe:
        return False
    try:
        proc = subprocess.run(
            [exe, "convert", str(src), "--to", "docx", "-o", str(dest.parent), "--overwrite", "--quiet"],
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("filee pdf to docx failed: %s", exc)
        return False
    produced = dest.parent / f"{src.stem}.docx"
    if proc.returncode != 0 or not produced.is_file() or produced.stat().st_size < 1000:
        err = (proc.stderr or proc.stdout or "").strip()
        if err:
            logger.warning("filee pdf to docx: %s", err[:300])
        return False
    if produced.resolve() != dest.resolve():
        shutil.move(str(produced), dest)
    return dest.is_file() and dest.stat().st_size > 1000


def _via_pdf2docx(src: Path, dest: Path) -> None:
    from pdf2docx import Converter

    cv = Converter(str(src))
    try:
        cv.convert(str(dest), multi_processing=False)
    finally:
        cv.close()
    if not dest.is_file() or dest.stat().st_size < 1000:
        raise RuntimeError("PDF 转 Word 没有生成有效文件")
