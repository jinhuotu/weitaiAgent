"""File-backed catalog for cable source documents.

The catalog is intentionally small and deterministic: originals are copied
once by content hash, while parsed blocks are stored in a JSON index.  It can
later be replaced by a database repository without changing the ingest model.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

from .document_ingest import IngestedDocument, ingest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def build_entry(source: str | Path, document: IngestedDocument | None = None) -> dict[str, Any]:
    """Build a stable catalog entry without writing anything."""
    path = Path(source)
    parsed = document or ingest(path)
    return {
        "document_id": _sha256(path),
        "source_file": path.name,
        "file_type": parsed.file_type,
        "size": path.stat().st_size,
        "blocks": _jsonable(parsed.blocks),
        "warnings": list(parsed.warnings),
    }


def persist(source: str | Path, catalog_dir: str | Path) -> dict[str, Any]:
    """Copy an original once and atomically update the JSON catalog index."""
    source_path = Path(source)
    root = Path(catalog_dir)
    originals = root / "originals"
    originals.mkdir(parents=True, exist_ok=True)
    index_path = root / "catalog.json"
    entry = build_entry(source_path)
    document_id = entry["document_id"]
    stored_name = f"{document_id}{source_path.suffix.lower()}"
    stored_path = originals / stored_name
    if not stored_path.exists():
        shutil.copy2(source_path, stored_path)

    existing: list[dict[str, Any]] = []
    if index_path.exists():
        existing = json.loads(index_path.read_text(encoding="utf-8"))
    entries = {item["document_id"]: item for item in existing}
    entry["original_path"] = str(Path("originals") / stored_name)
    entries[document_id] = entry
    payload = [entries[key] for key in sorted(entries)]
    temp_path = index_path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(index_path)
    return entry


def load_catalog(catalog_dir: str | Path) -> list[dict[str, Any]]:
    path = Path(catalog_dir) / "catalog.json"
    if not path.exists():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("catalog.json must contain a list")
    return value
