"""Cable tender chapter planning.

The original tender TOC is the sole source of top-level chapters.  Scoring
criteria are annotations on those chapters; they never create chapters.
This module deliberately has no dependency on the legacy tender services.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable, Mapping, Sequence


_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
        "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12, "十三": 13,
        "十四": 14, "十五": 15, "十六": 16, "十七": 17, "十八": 18,
        "十九": 19, "二十": 20}
_PREFIX = re.compile(r"^\s*(?:第\s*)?([一二三四五六七八九十百零〇两\d]+)\s*(?:章|、|\.|．|\)|）)\s*")


def _value(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, Mapping):
        return str(item.get("title") or item.get("chapter_title") or item.get("chapter") or item.get("name") or "").strip()
    return str(getattr(item, "title", getattr(item, "name", item))).strip()


def _number(title: str) -> int | None:
    match = _PREFIX.match(title)
    if not match:
        return None
    raw = match.group(1)
    if raw.isdigit():
        return int(raw)
    if raw in _NUM:
        return _NUM[raw]
    if raw.startswith("十"):
        return 10 + _NUM.get(raw[1:], 0)
    if raw.endswith("十"):
        return _NUM.get(raw[:-1], 0) * 10
    if "十" in raw:
        left, right = raw.split("十", 1)
        return _NUM.get(left, 0) * 10 + _NUM.get(right, 0)
    return None


def _key(title: str) -> str:
    title = re.sub(r"^\s*(?:第\s*)?[一二三四五六七八九十百零〇两\d]+\s*(?:章|、|\.|．|\)|）)\s*", "", title)
    return re.sub(r"\s+", "", title).strip(" ：:、")


def _bigrams(text: str) -> set[str]:
    compact = re.sub(r"\s+", "", text)
    if len(compact) < 2:
        return {compact} if compact else set()
    return {compact[i:i + 2] for i in range(len(compact) - 1)}


def bigram_similarity(left: str, right: str) -> float:
    """Dice similarity over 2-grams, bounded to [0, 1]."""
    a, b = _bigrams(left), _bigrams(right)
    if not a or not b:
        return 0.0
    return 2 * len(a & b) / (len(a) + len(b))


@dataclass(frozen=True)
class ChapterAssignment:
    criterion_id: str
    criterion: str
    chapter: str
    similarity: float
    notes: str = ""


@dataclass(frozen=True)
class ChapterPlan:
    original_toc: tuple[str, ...]
    chapters: tuple[str, ...]
    assignments: tuple[ChapterAssignment, ...] = field(default_factory=tuple)
    volume_chapters: dict[str, tuple[str, ...]] = field(default_factory=dict)


def assert_chapter_plan(chapters: Sequence[Any], original_toc: Sequence[Any]) -> None:
    """Fail closed when generated top-level chapters diverge from the source TOC."""
    actual = [_value(x) for x in chapters if _value(x)]
    original = [_value(x) for x in original_toc if _value(x)]
    expected_keys = [_key(x) for x in original]
    actual_keys = [_key(x) for x in actual]
    missing = [x for x in original if _key(x) not in actual_keys]
    extra = [x for x in actual if _key(x) not in expected_keys]
    expected_numbers = [_number(x) for x in original]
    actual_numbers = [_number(x) for x in actual]
    # A generated subset must preserve source order and cannot skip a number
    # between two generated chapters.
    positions = [expected_keys.index(k) for k in actual_keys if k in expected_keys]
    ordered = positions == sorted(positions)
    contiguous = (
        all(n is not None for n in actual_numbers)
        and all(b == a + 1 for a, b in zip(actual_numbers, actual_numbers[1:]))
        and actual_numbers == [expected_numbers[i] for i in positions]
    )
    if missing or extra or not contiguous or not ordered:
        diff = {"missing": missing, "extra": extra, "original": original,
                "generated": actual, "original_numbers": expected_numbers,
                "generated_numbers": actual_numbers}
        raise ValueError(f"章节计划校验失败 diff={diff}")


def assign_scoring_items(criteria: Iterable[Any], original_toc: Sequence[Any], *, threshold: float = 0.6) -> tuple[ChapterAssignment, ...]:
    """Attach every scoring item to an existing TOC chapter, never create one."""
    toc = [_value(x) for x in original_toc if _value(x)]
    if not toc:
        raise ValueError("原版目录为空，无法挂载评分项")
    result: list[ChapterAssignment] = []
    for index, item in enumerate(criteria, start=1):
        if isinstance(item, Mapping):
            text = str(item.get("requirement") or item.get("name") or item.get("title") or "").strip()
            ident = str(item.get("criterion_id") or item.get("id") or f"score-{index:03d}")
        else:
            text = str(getattr(item, "requirement", getattr(item, "name", item))).strip()
            ident = str(getattr(item, "criterion_id", getattr(item, "id", f"score-{index:03d}")))
        scores = [(bigram_similarity(text, chapter), pos, chapter) for pos, chapter in enumerate(toc)]
        score, _, chapter = max(scores, key=lambda row: (row[0], -row[1]))
        notes = ""
        if score < threshold:
            technical = [row for row in scores if any(cue in row[2] for cue in ("技术", "实施", "方案", "组织", "措施"))]
            if technical:
                score, _, chapter = max(technical, key=lambda row: (row[0], -row[1]))
            notes = f"评分项 {ident} 未能确定归属章节，已挂到 {chapter}"
        result.append(ChapterAssignment(ident, text, chapter, score, notes))
    return tuple(result)


def build_chapter_plan(original_toc: Sequence[Any], generated_chapters: Sequence[Any], criteria: Iterable[Any] = ()) -> ChapterPlan:
    original = tuple(_value(x) for x in original_toc if _value(x))
    generated = tuple(_value(x) for x in generated_chapters if _value(x))
    assert_chapter_plan(generated, original)
    assignments = assign_scoring_items(criteria, original) if criteria else ()
    return ChapterPlan(original, generated, assignments)


def chapter_plan_report(plan: ChapterPlan) -> dict[str, Any]:
    """Return deterministic H1-H3 diagnostics for logs and acceptance tests."""
    original = list(plan.original_toc)
    generated = list(plan.chapters)
    return {
        "H1": {"original": original, "generated": generated,
               "missing": [x for x in original if _key(x) not in {_key(y) for y in generated}],
               "extra": [x for x in generated if _key(x) not in {_key(y) for y in original}]},
        "H2": {"continuous": all(n is not None for n in (_number(x) for x in generated)) and all(
            b == a + 1 for a, b in zip([_number(x) for x in generated], [_number(x) for x in generated][1:]))},
        "H3": [a.__dict__.copy() for a in plan.assignments],
    }


def acceptance_report(
    plan: ChapterPlan,
    *,
    technical_text: str = "",
    business_text: str = "",
    original_technical_toc: Sequence[Any] = (),
) -> dict[str, Any]:
    """Produce H1-H6 checks without mutating documents or inventing chapters."""
    report = chapter_plan_report(plan)
    tech_original = [_value(x) for x in original_technical_toc if _value(x)]
    tech_generated = list(plan.volume_chapters.get("technical", ()))
    debug_terms = ("其他资料" + "（待核对归属）", "其他资料" + "(待核对归属)")
    debug_hits = [term for term in debug_terms
                  if term in (business_text + technical_text)]
    qualification_terms = ("安全生产许可证", "项目经理B证", "项目经理B证书")
    qualification_hits = [term for term in qualification_terms if term in technical_text]
    report["H4"] = {
        "independent": bool(tech_original),
        "original_technical": tech_original,
        "generated_technical": tech_generated if tech_original else [],
        "chapter_count_ok": not tech_original or len(tech_generated) >= len(tech_original),
    }
    report["H5"] = {"hits": debug_hits, "ok": not debug_hits}
    report["H6"] = {"hits": qualification_hits, "ok": not qualification_hits}
    return report

