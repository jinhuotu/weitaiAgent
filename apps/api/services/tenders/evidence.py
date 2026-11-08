"""Requirement driven evidence retrieval for tender generation."""
from __future__ import annotations
from dataclasses import dataclass
from collections import Counter
from math import log, log1p
import re
from typing import Iterable
from common.config import get_settings

@dataclass(frozen=True)
class Evidence:
    file_id: str
    text: str
    page: int | None = None
    kind: str = ""
    subject_match: bool = False
    source: str = "uploaded_this_bid"
    seq: int = 0
    block_index: int | None = None
    block_total: int | None = None
    def marker(self) -> str:
        if self.source == "library":
            label = f"资料库·{self.file_id}"
        else:
            suffix = f"第{self.page}页" if self.page else "页码未标注"
            if not self.page and self.block_index is not None:
                suffix = f"块 {self.block_index}/{self.block_total}"
            label = f"上传资料·{self.file_id}·{suffix}"
        return f"【{label}" + ("·待核验】" if not self.subject_match else "】")


def make_evidence(file_id, text, *, matched: bool, kind="", page=None,
                  source="uploaded_this_bid", seq=0, block_index=None, block_total=None) -> Evidence:
    return Evidence(file_id, text, page=page, kind=kind, subject_match=matched,
                    source=source, seq=seq, block_index=block_index, block_total=block_total)


def evidence_chunks(text: str) -> Iterable[str]:
    for paragraph in re.split(r"\r?\n(?:[^\S\r\n]*\r?\n)+", text.strip()):
        if not paragraph:
            continue
        if len(paragraph) <= 600:
            yield paragraph
            continue
        for start in range(0, len(paragraph), 300):
            yield paragraph[start:start + 400]
            if start + 400 >= len(paragraph):
                break

def _grams(text: str, n: int) -> set[str]:
    chars = [c for c in text if not c.isspace() and not re.match(r"[，。；：、,.!?！？（）()\[\]【】]", c)]
    return {"".join(chars[i:i+n]).lower() for i in range(max(0, len(chars)-n+1))}
def _tokens(text: str) -> set[str]:
    return {x.lower() for x in re.findall(r"[A-Za-z0-9][A-Za-z0-9_./-]*", text)}
def retrieve(requirement: str, records: Iterable[Evidence], *, limit: int = 3, threshold: int | None = None,
             requirement_kind: str = "", diagnostics: dict | None = None) -> list[Evidence]:
    settings = get_settings()
    threshold = settings.evidence_score_threshold if threshold is None else threshold
    bi, tri, tok = _grams(requirement, 2), _grams(requirement, 3), _tokens(requirement)
    bi.difference_update(word.strip() for word in settings.evidence_stop_grams.split(","))
    threshold_eff = min(threshold, max(3, len(bi) // 2))
    # An explicit short label identifies the subject of a numbered or tabular
    # requirement. Shared dates or boilerplate alone cannot establish that subject.
    label = re.match(r"^(?:\d+[.、]\s*)?([\u4e00-\u9fff]{2,12})\s*[:：|]", requirement)
    anchor = label.group(1).removesuffix("要求") if label else ""
    chinese_bigrams = {gram for gram in bi if re.fullmatch(r"[\u4e00-\u9fff]{2}", gram)}
    candidates = []
    df = Counter()
    for rec in records:
        text = rec.text.lower()
        matched = {gram for gram in bi if gram in text}
        df.update(matched)
        candidates.append((rec, text, matched))
    weights = {gram: log((len(candidates) + 1) / (count + 1)) + 1 for gram, count in df.items()}
    scored = []
    for rec, text, matched in candidates:
        if (anchor and anchor not in text) or (chinese_bigrams and not matched & chinese_bigrams):
            continue
        raw = (sum(weights[g] for g in sorted(matched)) * settings.evidence_bigram_weight
               + sum(g in text for g in tri) * settings.evidence_trigram_weight
               + len(tok & _tokens(text)) * settings.evidence_token_weight)
        denom = min(settings.evidence_length_penalty_cap,
                    1 + log1p(len(rec.text) / settings.evidence_length_scale) ** 0.5)
        score = raw / denom
        if score >= threshold_eff:
            scored.append((score, bool(requirement_kind and rec.kind == requirement_kind), rec.subject_match, rec.page or 10**9, rec.seq, rec))
    scored.sort(key=lambda x: (-x[0], -int(x[1]), -int(x[2]), x[3], x[4]))
    top = scored[0][0] if scored else 0
    floor = max(threshold_eff, top * settings.evidence_relative_floor)
    kept = [row for row in scored if row[0] >= floor]
    hits = kept[:max(0, limit)]
    if diagnostics is not None:
        diagnostics.update(top_score=top, kept=len(kept),
                           dropped=[row[0] for row in scored if row[0] < floor],
                           hits=[(row[-1].file_id, row[0]) for row in hits],
                           records=[(rec.file_id, rec.page, rec.block_index, round(row[0], 2), rec.marker())
                                    for row in hits for rec in (row[-1],)])
    return [row[-1] for row in hits]
def format_records(records: Iterable[Evidence], *, budget: int = 8000) -> str:
    lines=[]
    for rec in records:
        line=f"{rec.marker()} {rec.text[:500]}"
        if len("\n".join(lines))+len(line)>budget: break
        lines.append(line)
    return "\n".join(lines)
