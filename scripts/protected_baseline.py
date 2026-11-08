# -*- coding: utf-8 -*-
"""受保护模块基线指纹。

用途：证明 cablepkg 开发期间没有改动通用标书与压缩包旧模块。
做法：对受保护路径下每个文件算 SHA-256，按相对路径排序后整体再算一次摘要。

用法：
    python scripts/protected_baseline.py            # 生成/刷新基线
    python scripts/protected_baseline.py --check    # 比对，有差异则退出码 1
    python scripts/protected_baseline.py --check --quiet

受保护范围（改任何一项都必须先显式解锁，见 AGENTS.md）：
    apps/api/services/tenders/**
    apps/api/services/tender_package/**
    apps/api/routers/tenders.py
    E:/code/python/weitaiAgentui/src/views/TendersView.vue（外部仓库，存在时纳入）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "storage" / "baselines" / "protected.sha256.json"

PROTECTED_DIRS = [
    "apps/api/services/tenders",
    "apps/api/services/tender_package",
]
PROTECTED_FILES = [
    "apps/api/routers/tenders.py",
]
EXTERNAL_FILES = [
    "D:/code/LLM/weitaiAgentui/src/views/TendersView.vue",
    "E:/code/python/weitaiAgentui/src/views/TendersView.vue",
]

SKIP_DIR_NAMES = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    data = path.read_bytes().replace(b"\r\n", b"\n")
    h.update(data)
    return h.hexdigest()


def iter_files(base: Path):
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        if any(part in SKIP_DIR_NAMES for part in p.parts):
            continue
        if p.suffix.lower() in SKIP_SUFFIXES:
            continue
        yield p


def collect() -> dict[str, str]:
    entries: dict[str, str] = {}
    for rel in PROTECTED_DIRS:
        base = ROOT / rel
        if not base.is_dir():
            continue
        for p in iter_files(base):
            entries[p.relative_to(ROOT).as_posix()] = sha256_file(p)
    for rel in PROTECTED_FILES:
        p = ROOT / rel
        if p.is_file():
            entries[rel] = sha256_file(p)
    external_found = False
    for raw in EXTERNAL_FILES:
        p = Path(raw)
        if p.is_file():
            external_found = True
            root = next((parent for parent in p.parents if parent.name == "weitaiAgentui"), p.parent)
            entries[f"external::{p.relative_to(root).as_posix()}"] = sha256_file(p)
    if not external_found:
        print("[SKIP] 外部保护文件未找到，已跳过")
    return dict(sorted(entries.items()))


def digest_of(entries: dict[str, str]) -> str:
    h = hashlib.sha256()
    for name, value in entries.items():
        h.update(f"{name}\0{value}\n".encode("utf-8"))
    return h.hexdigest()


def write_baseline(path: Path, entries: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "protected-baseline/1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(ROOT),
        "file_count": len(entries),
        "digest": digest_of(entries),
        "files": entries,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def check(path: Path, entries: dict[str, str], quiet: bool) -> int:
    if not path.is_file():
        print(f"[FAIL] 基线不存在：{path}；先不带 --check 运行一次", file=sys.stderr)
        return 2
    baseline = json.loads(path.read_text(encoding="utf-8"))
    old = baseline.get("files", {})
    added = sorted(set(entries) - set(old))
    removed = sorted(set(old) - set(entries))
    changed = sorted(k for k in set(old) & set(entries) if old[k] != entries[k])

    if not (added or removed or changed):
        if not quiet:
            print(f"[OK] 受保护文件未变动：{len(entries)} 个，摘要 {digest_of(entries)[:16]}…")
            print(f"     基线时间：{baseline.get('generated_at', '未知')}")
        return 0

    print("[FAIL] 受保护模块被改动：", file=sys.stderr)
    for label, items in (("新增", added), ("删除", removed), ("修改", changed)):
        for name in items[:50]:
            print(f"  {label}  {name}", file=sys.stderr)
        if len(items) > 50:
            print(f"  {label}  …… 另有 {len(items) - 50} 个", file=sys.stderr)
    print(
        "\n如确需修改，请在提交信息中写明理由并刷新基线：\n"
        "    python scripts/protected_baseline.py",
        file=sys.stderr,
    )
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    path = Path(args.out)
    entries = collect()
    if args.check:
        return check(path, entries, args.quiet)
    write_baseline(path, entries)
    print(f"[OK] 已写入基线：{path}")
    print(f"     受保护文件 {len(entries)} 个，摘要 {digest_of(entries)[:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
