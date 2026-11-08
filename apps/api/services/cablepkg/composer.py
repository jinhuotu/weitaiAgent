"""Cable bid-section composition with hidden provenance.

The public result is clean bid prose.  Provenance is retained only in the
in-memory draft for validation/audit and is never rendered into the document.
No LLM or persistence dependency is required; callers may inject a generator.
"""

from dataclasses import dataclass, field
import re
from typing import Callable, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class SourceSnippet:
    text: str
    source_id: str = ""
    evidence_level: str = "L4"


@dataclass(frozen=True)
class SectionRequest:
    title: str
    requirement: str = ""
    template: str = ""
    snippets: tuple[SourceSnippet, ...] = ()


@dataclass
class SectionDraft:
    title: str
    content: str
    source_ids: list[str] = field(default_factory=list)
    evidence_levels: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def public_text(self) -> str:
        """Return exactly what may be inserted into the tender document."""
        return self.content


Generator = Callable[[str], str]


_SOURCE_LINE = re.compile(r"^\s*(?:来源|出处|source|citation)\s*[:：].*$", re.I)
_INTERNAL_MARKER = re.compile(r"\[(?:source|来源)\s*:[^\]]+\]", re.I)


def build_prompt(request: SectionRequest) -> str:
    """Build a grounded prompt without asking the model to invent facts."""
    evidence = "\n".join(f"- {item.text}" for item in request.snippets if item.text.strip())
    return (
        "你是10kV电力电缆投标文件编写器。仅根据给定资料组织成稿，不得新增资料中没有的数值、证书、检测结论或承诺。"
        "对缺失事实使用‘需补充’并保持章节正式、连贯。不要输出来源、引用、提示词或解释。\n"
        f"章节：{request.title}\n招标要求：{request.requirement}\n章节模板：{request.template}\n"
        f"可用资料：\n{evidence}"
    )


def _clean_public_text(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        if _SOURCE_LINE.match(line):
            continue
        line = _INTERNAL_MARKER.sub("", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines).strip()


def compose_section(request: SectionRequest, generator: Generator | None = None) -> SectionDraft:
    """Compose one section; deterministic fallback keeps tests/offline mode usable."""
    prompt = build_prompt(request)
    if generator is None:
        parts = [item.text.strip() for item in request.snippets if item.text.strip()]
        content = "\n".join(parts) if parts else "需补充本章节内容。"
    else:
        content = generator(prompt)
    clean = _clean_public_text(content)
    levels = sorted({item.evidence_level for item in request.snippets})
    warnings = []
    if not request.snippets:
        warnings.append("本章节没有资料库依据，需人工补充")
    if any(level not in {"L0", "L1"} for level in levels):
        warnings.append("章节资料包含L2-L4证据，不能单独证明可投标")
    return SectionDraft(
        title=request.title,
        content=clean,
        source_ids=[item.source_id for item in request.snippets if item.source_id],
        evidence_levels=levels,
        warnings=warnings,
    )


def compose_sections(requests: Sequence[SectionRequest], generator: Generator | None = None) -> list[SectionDraft]:
    return [compose_section(request, generator) for request in requests]
