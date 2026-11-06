"""Deterministic, read-only ingestion for cable bid source documents.

The parser keeps the original file as the source of truth and emits semantic
blocks for retrieval and field filling.  It deliberately does not convert
Word/Excel files to PDF; PDF is supported as an additional input format.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import re
from typing import Any, Iterable


@dataclass(frozen=True)
class SourceRef:
    file_name: str
    file_type: str
    location: str


@dataclass(frozen=True)
class TableRow:
    table_id: str
    headers: tuple[str, ...]
    values: tuple[str, ...]
    source: SourceRef


@dataclass(frozen=True)
class Placeholder:
    label: str
    raw: str
    source: SourceRef


@dataclass(frozen=True)
class DocumentBlock:
    kind: str  # paragraph, heading, table_row
    text: str
    source: SourceRef
    table: TableRow | None = None
    placeholders: tuple[Placeholder, ...] = ()


@dataclass
class IngestedDocument:
    source_file: str
    file_type: str
    blocks: list[DocumentBlock] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "file_type": self.file_type,
            "blocks": [asdict(block) for block in self.blocks],
            "warnings": list(self.warnings),
        }


_PLACEHOLDER = re.compile(r"(?:\{([^{}]+)\}|（([^（）]+)）|\[([^\[\]]+)\])")
_HEADING = re.compile(r"^(?:第[一二三四五六七八九十百]+[章节部分]|[一二三四五六七八九十]+、|\d+(?:\.\d+)*)")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _placeholders(text: str, source: SourceRef) -> tuple[Placeholder, ...]:
    result = []
    for match in _PLACEHOLDER.finditer(text):
        raw = match.group(0)
        label = next((item.strip() for item in match.groups() if item), raw)
        result.append(Placeholder(label=label, raw=raw, source=source))
    return tuple(result)


def _block(kind: str, text: str, source: SourceRef, table: TableRow | None = None) -> DocumentBlock:
    return DocumentBlock(kind=kind, text=text, source=source, table=table, placeholders=_placeholders(text, source))


def _normalise_row(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(_text(value) for value in values)


def parse_docx(path: str | Path) -> IngestedDocument:
    from docx import Document

    source_path = Path(path)
    result = IngestedDocument(source_path.name, "docx")
    document = Document(source_path)
    for index, paragraph in enumerate(document.paragraphs, 1):
        text = paragraph.text.strip()
        if not text:
            continue
        kind = "heading" if paragraph.style.name.lower().startswith("heading") or _HEADING.match(text) else "paragraph"
        source = SourceRef(source_path.name, "docx", f"paragraph:{index}")
        result.blocks.append(_block(kind, text, source))
    for table_index, table in enumerate(document.tables, 1):
        rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        if not rows:
            continue
        headers = _normalise_row(rows[0])
        for row_index, values in enumerate(rows[1:], 2):
            normal = _normalise_row(values)
            source = SourceRef(source_path.name, "docx", f"table:{table_index}:row:{row_index}")
            table_row = TableRow(f"table-{table_index}", headers, normal, source)
            result.blocks.append(_block("table_row", " | ".join(normal), source, table_row))
    return result


def parse_xlsx(path: str | Path) -> IngestedDocument:
    from openpyxl import load_workbook

    source_path = Path(path)
    result = IngestedDocument(source_path.name, "xlsx")
    workbook = load_workbook(source_path, read_only=True, data_only=True)
    for worksheet in workbook.worksheets:
        rows = worksheet.iter_rows(values_only=True)
        headers = _normalise_row(next(rows, ()))
        if not any(headers):
            result.warnings.append(f"{worksheet.title}: empty header row")
            continue
        table_id = f"sheet-{worksheet.title}"
        for row_index, values in enumerate(rows, 2):
            normal = _normalise_row(values)
            if not any(normal):
                continue
            source = SourceRef(source_path.name, "xlsx", f"sheet:{worksheet.title}:row:{row_index}")
            table_row = TableRow(table_id, headers, normal, source)
            result.blocks.append(_block("table_row", " | ".join(normal), source, table_row))
    return result


def parse_pdf(path: str | Path) -> IngestedDocument:
    from pypdf import PdfReader

    source_path = Path(path)
    result = IngestedDocument(source_path.name, "pdf")
    reader = PdfReader(str(source_path))
    for page_index, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        if not text:
            result.warnings.append(f"page:{page_index}: no extractable text (OCR may be required)")
            continue
        for line_index, line in enumerate((line.strip() for line in text.splitlines()), 1):
            if line:
                source = SourceRef(source_path.name, "pdf", f"page:{page_index}:line:{line_index}")
                kind = "heading" if _HEADING.match(line) else "paragraph"
                result.blocks.append(_block(kind, line, source))
    return result


def ingest(path: str | Path) -> IngestedDocument:
    """Parse a supported source without changing it or creating a PDF copy."""
    suffix = Path(path).suffix.lower()
    parsers = {".docx": parse_docx, ".xlsx": parse_xlsx, ".xlsm": parse_xlsx, ".pdf": parse_pdf}
    try:
        parser = parsers[suffix]
    except KeyError as exc:
        raise ValueError(f"unsupported document type: {suffix or '<none>'}") from exc
    return parser(path)
