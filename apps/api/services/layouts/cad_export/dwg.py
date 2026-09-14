"""可选 DXF→DWG：调用本机 ODA / Teigha File Converter，不在 Python 里写 DWG。"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from common.config import get_settings

_CANDIDATES = (
    r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe",
    r"C:\Program Files (x86)\ODA\ODAFileConverter\ODAFileConverter.exe",
    r"C:\Program Files\ODA\Teigha File Converter\TeighaFileConverter.exe",
)


def resolve_oda_converter() -> Path | None:
    configured = (getattr(get_settings(), "cad_oda_converter", "") or "").strip()
    names = [configured] if configured else []
    names.extend(_CANDIDATES)
    names.append("ODAFileConverter")
    names.append("TeighaFileConverter")
    for raw in names:
        if not raw:
            continue
        path = Path(raw).expanduser()
        if path.is_file():
            return path.resolve()
        found = shutil.which(raw)
        if found:
            return Path(found).resolve()
    return None


def write_plan_dwg(dxf_path: Path, *, timeout_s: float = 60.0) -> Path | None:
    """把已写出的 DXF 转成同名 DWG。没有转换器则返回 None。

    在临时目录里转，避免 ODA 把整个 layouts 目录的 DXF 都转一遍。
    """
    src = Path(dxf_path).expanduser().resolve()
    if not src.is_file() or src.suffix.lower() != ".dxf":
        return None
    converter = resolve_oda_converter()
    if converter is None:
        return None
    dest = src.with_suffix(".dwg")
    with tempfile.TemporaryDirectory(prefix="weitai-dwg-") as tmp:
        tdir = Path(tmp)
        copied = tdir / src.name
        shutil.copy2(src, copied)
        try:
            subprocess.run(  # noqa: S603
                [
                    str(converter),
                    str(tdir),
                    str(tdir),
                    "ACAD2013",
                    "DWG",
                    "0",
                    "1",
                ],
                check=False,
                capture_output=True,
                timeout=timeout_s,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        produced = tdir / f"{src.stem}.dwg"
        if not produced.is_file() or produced.stat().st_size <= 0:
            return None
        shutil.copy2(produced, dest)
    return dest if dest.is_file() and dest.stat().st_size > 0 else None
