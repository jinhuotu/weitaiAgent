"""只读预览：本机 WPS / Word / LibreOffice 把 docx 打成 PDF。"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from common.config import get_settings
from common.errors import AppError, ErrorCode

logger = logging.getLogger("api.tenders.preview_pdf")

_LOCK = threading.Lock()
_PS_TIMEOUT = 90
_SOFFICE_TIMEOUT = 90

_PS_EXPORT = r"""
param($AppId, $InPath, $OutPath)
$ErrorActionPreference = 'Stop'
$app = New-Object -ComObject $AppId
try {
  try { $app.Visible = $false } catch {}
  try { $app.DisplayAlerts = 0 } catch {}
  $doc = $app.Documents.Open($InPath, $false, $true)
  try {
    try { $doc.Repaginate() } catch {}
    try { foreach ($toc in $doc.TablesOfContents) { $toc.Update() } } catch {}
    try { $doc.Fields.Update() } catch {}
    $ok = $false
    try { $doc.ExportAsFixedFormat($OutPath, 17); $ok = $true } catch {}
    if (-not $ok) {
      try { $doc.SaveAs($OutPath, 17); $ok = $true } catch {}
    }
    if (-not $ok) { throw 'export pdf failed' }
  } finally {
    $doc.Close($false)
  }
} finally {
  if ($app.Documents.Count -eq 0) {
    try { $app.Quit() } catch {}
  }
  [System.Runtime.InteropServices.Marshal]::ReleaseComObject($app) | Out-Null
}
"""


def preview_pdf_path(docx: Path) -> Path:
    return docx.with_name(f"{docx.stem}.preview.pdf")


def unlink_preview_pdf(docx_name: str) -> None:
    from api.services.tenders.assets import tenders_output_dir

    name = (docx_name or "").strip()
    if not name.lower().endswith(".docx"):
        return
    path = (tenders_output_dir() / f"{Path(name).stem}.preview.pdf").resolve()
    root = tenders_output_dir().resolve()
    try:
        if path.is_file() and path.is_relative_to(root):
            path.unlink(missing_ok=True)
    except OSError:
        pass


def ensure_preview_pdf(docx: Path) -> Path:
    dest = preview_pdf_path(docx)
    with _LOCK:
        if _fresh(docx, dest):
            return dest
        kind = (get_settings().tender_preview_converter or "auto").strip().lower()
        if kind in {"off", "none", "browser"}:
            raise AppError(
                ErrorCode.BAD_REQUEST,
                "未启用本机排版预览（TENDER_PREVIEW_CONVERTER=off）",
                status_code=503,
            )
        _convert(docx, dest, kind)
        if not dest.is_file() or dest.stat().st_size < 32:
            raise AppError(ErrorCode.INTERNAL, "预览 PDF 生成失败", status_code=500)
        return dest


def _fresh(docx: Path, pdf: Path) -> bool:
    if not pdf.is_file() or pdf.stat().st_size < 32:
        return False
    try:
        return pdf.stat().st_mtime >= docx.stat().st_mtime
    except OSError:
        return False


def _convert(docx: Path, dest: Path, kind: str) -> None:
    order = _order(kind)
    errors: list[str] = []
    tmp = dest.with_suffix(".tmp.pdf")
    tmp.unlink(missing_ok=True)
    for app in order:
        try:
            if app in {"wps", "word"}:
                _com_export(app, docx, tmp)
            else:
                _soffice_export(docx, tmp)
            if tmp.is_file() and tmp.stat().st_size >= 32:
                tmp.replace(dest)
                logger.info("preview pdf via %s file=%s bytes=%s", app, dest.name, dest.stat().st_size)
                return
            errors.append(f"{app}: empty output")
        except Exception as exc:
            logger.warning("preview convert %s failed: %s", app, exc)
            errors.append(f"{app}: {exc}")
            tmp.unlink(missing_ok=True)
    dest.unlink(missing_ok=True)
    raise AppError(
        ErrorCode.INTERNAL,
        "无法用本机 WPS/Word/LibreOffice 生成预览。"
        "请安装 WPS 或把 TENDER_PREVIEW_CONVERTER 设为已安装的程序。"
        + ((" " + "；".join(errors[:3])) if errors else ""),
        status_code=503,
    )


def _order(kind: str) -> list[str]:
    if kind in {"wps", "kwps"}:
        return ["wps"]
    if kind in {"word", "winword"}:
        return ["word"]
    if kind in {"soffice", "libreoffice", "lo"}:
        return ["soffice"]
    return ["wps", "word", "soffice"]


def _com_export(app: str, docx: Path, dest: Path) -> None:
    progid = "Kwps.Application" if app == "wps" else "Word.Application"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8") as fh:
        fh.write(_PS_EXPORT)
        script = Path(fh.name)
    try:
        r = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-AppId",
                progid,
                "-InPath",
                str(docx.resolve()),
                "-OutPath",
                str(dest.resolve()),
            ],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT,
        )
        if r.returncode != 0 or not dest.is_file():
            err = (r.stderr or r.stdout or "").strip()[:400]
            raise RuntimeError(err or f"{progid} exit {r.returncode}")
    finally:
        script.unlink(missing_ok=True)


def _soffice_export(docx: Path, dest: Path) -> None:
    exe = _soffice_bin()
    if not exe:
        raise RuntimeError("未找到 soffice")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as raw:
        out_dir = Path(raw)
        r = subprocess.run(
            [
                exe,
                "--headless",
                "--norestore",
                "--convert-to",
                "pdf:writer_pdf_Export",
                "--outdir",
                str(out_dir),
                str(docx.resolve()),
            ],
            capture_output=True,
            text=True,
            timeout=_SOFFICE_TIMEOUT,
        )
        produced = out_dir / f"{docx.stem}.pdf"
        if r.returncode != 0 or not produced.is_file():
            err = (r.stderr or r.stdout or "").strip()[:400]
            raise RuntimeError(err or f"soffice exit {r.returncode}")
        shutil.copy2(produced, dest)


def _soffice_bin() -> str | None:
    found = shutil.which("soffice") or shutil.which("soffice.exe")
    if found:
        return found
    for p in (
        Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
        Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
        Path("/usr/bin/soffice"),
        Path("/usr/lib/libreoffice/program/soffice"),
    ):
        if p.is_file():
            return str(p)
    return None
