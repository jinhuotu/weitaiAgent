"""Shared legacy Word conversion; no dependency on tender or knowledge services."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LEGACY_WORD_HINT = "旧版 .doc 打不开，请在 Word 里另存为 .docx 后再上传"


class WordConversionError(RuntimeError):
    """No converter produced a valid Word document within the time limit."""


def _word_com_supported() -> bool:
    return os.name == "nt"


def _run_converter(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    """Bound the entire converter process tree, without touching other Office sessions."""
    options = (
        {"creationflags": subprocess.CREATE_NO_WINDOW}
        if os.name == "nt"
        else {
            "start_new_session": True,
        }
    )
    with subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **options,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=max(0.01, timeout))
        except subprocess.TimeoutExpired:
            if process.poll() is None:
                try:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True,
                            timeout=10,
                            check=False,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                except (OSError, subprocess.SubprocessError):
                    pass
                finally:
                    if process.poll() is None:
                        process.kill()
            process.communicate(timeout=5)
            raise
        return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


def _validate_docx(path: Path) -> None:
    from docx import Document

    if not path.is_file() or path.stat().st_size == 0:
        raise WordConversionError("converter did not produce a DOCX")
    try:
        # Opening the OPC package checks document relationships/XML, not just a PK prefix.
        Document(str(path))
    except Exception as exc:
        raise WordConversionError("converter produced an invalid DOCX") from exc


def _word_com_convert(src: Path, dest: Path, pid_file: Path) -> None:
    """Runs only in a disposable worker process; COM initialization belongs to its thread."""
    import pythoncom
    import win32com.client
    import win32process

    word = None
    opened = None
    pythoncom.CoInitialize()
    try:
        before = set(win32process.EnumProcesses())
        word = win32com.client.DispatchEx("Word.Application")
        pid = _new_office_process(before)
        if pid is not None:
            pid_file.write_text(str(pid), encoding="ascii")
        word.Visible = False
        word.DisplayAlerts = 0
        word.AutomationSecurity = 3
        opened = word.Documents.Open(
            str(src.resolve()),
            ConfirmConversions=False,
            ReadOnly=True,
            AddToRecentFiles=False,
        )
        try:
            save = opened.SaveAs2
        except AttributeError:
            save = opened.SaveAs
        save(str(dest.resolve()), FileFormat=12)
    finally:
        try:
            if opened is not None:
                try:
                    opened.Close(False)
                finally:
                    opened = None
        finally:
            try:
                if word is not None:
                    word.Quit()
            finally:
                word = None
                pythoncom.CoUninitialize()


def _new_office_process(before: set[int]) -> int | None:
    """Word has no Application.Hwnd on some supported automation interfaces."""
    import win32api
    import win32process

    candidates = []
    for pid in set(win32process.EnumProcesses()) - before:
        handle = None
        try:
            handle = win32api.OpenProcess(0x0400 | 0x0010, False, pid)
            image = Path(win32process.GetModuleFileNameEx(handle, 0)).name.lower()
            if image in {"winword.exe", "wps.exe"}:
                candidates.append(pid)
        except Exception:
            continue
        finally:
            if handle is not None:
                handle.Close()
    return candidates[0] if len(candidates) == 1 else None


def _stop_owned_word(pid_file: Path) -> None:
    """Only the PID recorded by this worker's DispatchEx instance can be terminated."""
    if os.name != "nt" or not pid_file.is_file():
        return
    try:
        pid = int(pid_file.read_text(encoding="ascii"))
        if pid > 0:
            os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError):
        pass


def convert_doc_to_docx(src: Path, dest: Path, *, timeout: float = 120) -> Path:
    """Convert once, validate, then atomically publish; never alter the source file."""
    src, dest = Path(src).resolve(), Path(dest).resolve()
    if not src.is_file() or src == dest:
        raise WordConversionError(LEGACY_WORD_HINT)
    dest.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.01, timeout)
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="word-convert-", dir=dest.parent) as temp:
        folder = Path(temp)
        soffice = shutil.which("soffice") or shutil.which("soffice.exe")
        if soffice:
            try:
                budget = max(0.01, deadline - time.monotonic())
                if os.name == "nt":
                    budget = min(budget, max(0.01, timeout * 0.6))
                result = _run_converter(
                    [
                        soffice,
                        f"-env:UserInstallation={(folder / 'profile').as_uri()}",
                        "--headless",
                        "--convert-to",
                        "docx",
                        "--outdir",
                        str(folder),
                        str(src),
                    ],
                    budget,
                )
                if result.returncode:
                    raise WordConversionError(result.stderr[:500] or "LibreOffice failed")
                produced = folder / f"{src.stem}.docx"
                _validate_docx(produced)
                os.replace(produced, dest)
                return dest
            except (OSError, subprocess.SubprocessError, WordConversionError) as exc:
                errors.append(f"LibreOffice: {exc}")
        remaining = deadline - time.monotonic()
        if _word_com_supported() and remaining > 0:
            produced = folder / "word.docx"
            pid_file = folder / "word.pid"
            try:
                result = _run_converter(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--word-com",
                        str(src),
                        str(produced),
                        str(pid_file),
                    ],
                    remaining,
                )
                if result.returncode:
                    raise WordConversionError(result.stderr[-500:] or "Word COM failed")
                _validate_docx(produced)
                os.replace(produced, dest)
                return dest
            except (OSError, subprocess.SubprocessError, WordConversionError) as exc:
                errors.append(f"Word COM: {exc}")
                _stop_owned_word(pid_file)
        reason = "; ".join(errors) or "no converter available before deadline"
        raise WordConversionError(f"{LEGACY_WORD_HINT} ({reason})")


if __name__ == "__main__":
    if len(sys.argv) != 5 or sys.argv[1] != "--word-com":
        raise SystemExit("usage: word_convert.py --word-com source.doc output.docx pid-file")
    _word_com_convert(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
