"""Three-way response routing and scoring gates for cable tender prose."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Sequence

from .chapter_plan import bigram_similarity

L1 = "declare"
L2 = "declare_with_ref"
L3 = "provide_only"
_SCHEME = ("承诺", "保证", "措施", "方案", "体系", "计划", "管理", "控制", "组织", "部署", "安排", "程序", "流程", "目标", "建议", "对策", "理解", "说明", "服务", "配合", "协调", "应用", "技术措施")
_EVIDENCE = ("姓名", "证书编号", "注册号", "业绩数量", "合同金额", "净资产", "营业收入", "营业执照号")
_PROOF = ("证书", "执照", "报表", "合同", "截图", "发票", "许可证", "缴税凭证")
_STOP = set("的与及和或按根据本项目我方应当必须进行相关以及等其中要求提供是否用于对其" )
_NUM = re.compile(r"\d+(?:\.\d+)?\s*(?:小时|天|次|级|元|人|座|kV|%|年|日历天)?")


@dataclass(frozen=True)
class ResponseDecision:
    response_type: str
    pending_items: tuple[str, ...] = ()


def classify_response(item_name: str, criterion: str, *, chapter: str = "") -> str:
    """Classify by response semantics; defaults to declaration (L1)."""
    text = f"{item_name} {criterion}"
    if "服务承诺" in chapter or "服务承诺" in item_name:
        return L1
    scheme = any(word in text for word in _SCHEME)
    evidence = any(word in text for word in _EVIDENCE)
    proof = any(word in item_name for word in _PROOF)
    if proof and not scheme:
        return L3
    if scheme and evidence:
        return L2
    return L1


def _tokens(text: str) -> list[str]:
    text = re.sub(r"[\d①②③④⑤⑥⑦⑧⑨⑩一二三四五六七八九十]+(?:[、.)）]|分)?", " ", text)
    words = re.findall(r"[一-龥]{2,}|[A-Za-z]{2,}", text)
    return [word for word in words if word not in _STOP]


def criterion_terms(criterion: str) -> tuple[str, ...]:
    terms = _tokens(criterion)
    # Preserve meaningful compound labels while also checking their components.
    expanded: list[str] = []
    for term in terms:
        expanded.append(term)
        for marker in ("体系", "措施", "安排", "计划", "方案"):
            if marker in term and term != marker:
                expanded.append(marker)
    for marker in _SCHEME:
        if marker in criterion:
            expanded.append(marker)
    for marker in ("质量", "安全", "进度", "成本", "技术", "设计", "施工", "风险", "设备", "材料"):
        if marker in criterion:
            expanded.append(marker)
    return tuple(dict.fromkeys(expanded))


def validate_declaration(item_name: str, criterion: str, body: str, facts: Mapping[str, Any], *, response_type: str = L1) -> dict[str, Any]:
    """Apply J4/J5 gates to one declaration body."""
    body = body or ""
    terms = criterion_terms(criterion)
    hits = {term: term in body for term in terms}
    coverage = sum(hits.values()) / len(hits) if hits else 1.0
    fact_values = [str(value) for value in facts.values() if value not in (None, "")]
    cited_facts = [value for value in fact_values if value in body]
    numbers = _NUM.findall(body)
    bad_numbers = [number for number in numbers if not any(number in value for value in fact_values)]
    forbidden_people = [value for value in ("姓名", "证书编号", "业绩数量", "合同金额", "净资产", "营业收入") if value in body]
    ok = response_type == L3 or (coverage >= 0.8 and cited_facts and numbers and not bad_numbers and body.count("我方") >= 3 and not forbidden_people)
    return {"item": item_name, "response_type": response_type, "word_count": len(body),
            "coverage": coverage, "term_hits": hits, "cited_facts": cited_facts,
            "numbers": numbers, "bad_numbers": bad_numbers, "forbidden": forbidden_people, "ok": bool(ok)}


def route_item(item_name: str, criterion: str, facts: Mapping[str, Any], *, chapter: str = "") -> ResponseDecision:
    kind = classify_response(item_name, criterion, chapter=chapter)
    pending = tuple(key for key in ("人员姓名", "姓名", "证书号", "证书编号", "业绩", "财务") if key in f"{item_name}{criterion}")
    return ResponseDecision(kind, pending if kind == L2 else (pending if kind == L3 else ()))


def response_acceptance_report(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    reports = [dict(row) for row in rows]
    l1 = [row for row in reports if row.get("response_type") == L1]
    return {"J1": reports, "J1_ok": len(l1) >= 14 and not any(row.get("response_type") == L3 for row in reports),
            "J5": [{"item": row.get("item"), "missing": [term for term, hit in row.get("term_hits", {}).items() if not hit]} for row in reports],
            "J7_ok": all(bigram_similarity(str(a.get("body", "")), str(b.get("body", ""))) < 0.45 for i, a in enumerate(reports) for b in reports[i + 1:])}
