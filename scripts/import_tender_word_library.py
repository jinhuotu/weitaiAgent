"""Import Word sections into the configured tender library. Preview by default.

Run with the project virtualenv. Add --commit to append originals.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in ("apps", "packages"):
    sys.path.insert(0, str(ROOT / folder))

from sqlalchemy import select, text
from api.services.knowledge.access import require_tender_lib_manage
from api.services.knowledge.ingest import require_embedding_ready
from api.services.knowledge.parsers import extract_text_from_file
from api.services.tenders import library_kb as library
from api.services.tenders.material_identity import canonical_material_key
from api.services.tenders.schema import PlaceholderItem
from db.models.knowledge import KnowledgeBase, KnowledgeDocument
from db.models.user import User
from db.session import AsyncSessionLocal, engine


def section_title(path: Path) -> str:
    title = re.sub(r"^\d+_", "", path.stem)
    title = re.sub(r"^(?:价格|商务|技术)文件_", "", title)
    return re.sub(r"^\d+(?:\.\d+)*_", "", title).strip()


def matching_parent(title, parents):
    normalized = library._norm_material_title(title)
    exact = [p for p in parents if library._norm_material_title(p.name) == normalized]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise ValueError(f"资料标题存在多个匹配项：{title}")
    canonical = canonical_material_key(PlaceholderItem(key="", title=title))
    candidates = [p for p in parents if canonical and canonical_material_key(
        PlaceholderItem(key="", title=p.name)) == canonical]
    if len(candidates) > 1:
        raise ValueError(f"资料别名存在多个匹配项：{title}")
    return candidates[0] if candidates else None


async def documents(db, base_id):
    result = await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.base_id == base_id))
    return list(result.scalars().all())


async def import_files(args):
    source = args.source.resolve(strict=True)
    if not source.is_dir():
        raise ValueError("来源必须是目录")
    files = sorted(p for p in source.iterdir() if p.suffix.lower() in {".doc", ".docx"}
                   and not p.name.startswith(("~$", ".")))
    if not files:
        raise ValueError("目录中没有 Word 文件")
    report = {"source": str(source), "mode": "commit" if args.commit else "preview", "files": []}
    engine.echo = False
    try:
        async with engine.connect() as lock_connection, AsyncSessionLocal() as db:
            user = (await db.execute(select(User).where(
                User.username == args.username, User.is_active.is_(True)))).scalar_one()
            base = await require_tender_lib_manage(db, user)
            # Parse every original before the first write.
            prepared = []
            for path in files:
                raw = path.read_bytes()
                if len(raw) > library._MAX_BYTES:
                    raise ValueError(f"文件超过上传限制：{path.name}")
                parsed = await extract_text_from_file(path, ocr_budget=0)
                if not parsed.text.strip():
                    raise ValueError(f"文件无可解析正文：{path.name}")
                prepared.append((path, raw, hashlib.sha256(raw).hexdigest()))
            if args.commit:
                await require_embedding_ready(db)
            for path, raw, digest in prepared:
                title = section_title(path)
                row = {"file": path.name, "title": title, "sha256": digest}
                try:
                    # Serialize this import against other instances of this script.
                    lock_name = "tender-word-import:" + base.public_id
                    if args.commit:
                        acquired = (await lock_connection.execute(text("SELECT GET_LOCK(:name, 30)"),
                                                     {"name": lock_name})).scalar()
                        if acquired != 1:
                            raise RuntimeError("另一个导入正在运行")
                    try:
                        docs = await documents(db, base.id)
                        duplicate = next((d for d in docs if d.content_hash == digest
                                          and library._doc_file_path(d) is not None
                                          and hashlib.sha256(library._doc_file_path(d).read_bytes()).hexdigest() == digest), None)
                        if duplicate:
                            row.update(action="skipped", document_id=duplicate.public_id,
                                       status=duplicate.status)
                        else:
                            parents = [d for d in docs if library._is_material_parent(d)]
                            parent = matching_parent(title, parents)
                            row["action"] = "append" if parent else "create"
                            key = library.key_from_tags(parent.tags) if parent else "m" + hashlib.sha256(
                                library._norm_material_title(title).encode()).hexdigest()[:24]
                            row["key"] = key
                            if args.commit:
                                if parent is None:
                                    if any(library.key_from_tags(p.tags) == key for p in parents):
                                        raise ValueError("资料项 key 冲突")
                                    parent = library._new_parent(base_id=base.id,
                                        public_id=library.short_id(12), key=key, title=title,
                                        hint="Word 分册资料；含项目历史信息和待确认字段，使用前核对本次招标要求。")
                                    parent.created_by = user.id
                                    db.add(parent)
                                    await db.commit()
                                await library.save_library_file(db, key, filename=path.name,
                                    data=raw, replace=False, material_title=title)
                                stored = next(d for d in await documents(db, base.id)
                                              if d.parent_id == parent.public_id and d.content_hash == digest)
                                stored.created_by = user.id
                                stored.uploader = user.display_name or user.username
                                await db.commit()
                                original, _, _ = await library.open_library_file(db, stored.public_id)
                                if hashlib.sha256(original).hexdigest() != digest:
                                    raise RuntimeError("原件下载校验失败")
                                row.update(document_id=stored.public_id, status=stored.status)
                    finally:
                        if args.commit:
                            await lock_connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": lock_name})
                except Exception as exc:
                    await db.rollback()
                    row.update(action="failed", error=str(exc))
                report["files"].append(row)
                if args.commit:
                    args.report.parent.mkdir(parents=True, exist_ok=True)
                    temporary = args.report.with_suffix(".tmp")
                    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                    temporary.replace(args.report)
                print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        await engine.dispose()
    return 1 if any(row["action"] == "failed" for row in report["files"]) else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--username", required=True, help="拥有投标资料库维护权限的操作账号")
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--report", type=Path, default=ROOT / "storage/imports/tender-word-import.json")
    args = parser.parse_args()
    return asyncio.run(import_files(args))


if __name__ == "__main__":
    raise SystemExit(main())
