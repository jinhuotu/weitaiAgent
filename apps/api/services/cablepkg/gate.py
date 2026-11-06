"""Fail-closed evidence gate. Inputs are caller-owned rows and task metadata.

No seed, database or report is read. A rule violation creates its own blocker;
in particular design confirmation never disappears into an evidence blocker.
"""

from collections import Counter
from dataclasses import asdict, dataclass
import re
from typing import Any, Literal, Mapping, Sequence

from .derive import DerivationSkip, load_rules
from .model import Requirement, Response


@dataclass
class TaskMetadata:
    tender_package_name: str
    solidified_ids: list[str]


@dataclass
class Blocker:
    code: str
    solidified_id: str
    seq: str
    name: str
    reason: str
    priority: int
    row_index: int | None
    evidence_cannot_fix: bool = False


@dataclass
class GateResult:
    decision: Literal["biddable", "not_biddable", "needs_input"]
    blockers: list[Blocker]
    warnings: list[str]
    stats: dict[str, Any]

    @property
    def key_blockers(self) -> list[Blocker]:
        """Highest-priority G2 findings, also retained in the full blocker list."""
        return [blocker for blocker in self.blockers if blocker.code == "G2"]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["key_blockers"] = [asdict(blocker) for blocker in self.key_blockers]
        return result


def _material_conflict(requirement: Requirement, material: Mapping[str, Any]) -> str | None:
    """Compare explicit conductor wording with cond, never infer cond from model.

    Copper tape/wire and similar screen components are not conductor material
    requirements, including when mentioned alongside a conductor parameter.
    """
    raw = re.sub(r"[铜铝](?:带|丝|箔|网|屏蔽|接头|端子)", "", requirement.raw)
    conductor_name = "导体" in requirement.name and "屏蔽" not in requirement.name
    conductor_raw = re.search(r"[铜铝](?:芯|导体|截面)|导体材料", raw)
    if not conductor_name and not conductor_raw:
        return None
    expected = set(re.findall(r"[铜铝]", raw))
    cond = str(material.get("cond", "")).strip()
    # Aluminium-alloy metadata still belongs to the aluminium material family.
    actual = next((word for word in ("铜", "铝") if word in cond), None)
    if expected and actual and actual not in expected:
        return f"导体材料冲突：招标要求为{'或'.join(sorted(expected))}，物料cond为{cond}"
    return None


def _key_negative_response(row: Response | DerivationSkip) -> bool:
    if not isinstance(row, Response) or not row.is_key or row.guarantee_value is None:
        return False
    return bool(re.match(r"^\s*(?:不适用|不涉及|无此项|(?:无|—)(?=$|[\s（(：:，,；;。]))", str(row.guarantee_value)))


def evaluate_gate(
    rows: list[Response | DerivationSkip],
    task: TaskMetadata | Mapping[str, Any],
    *, forbidden_patterns: Sequence[str] | None = None,
    requirements: Sequence[Requirement] | None = None,
    materials: Mapping[str, Mapping[str, Any]] | None = None,
) -> GateResult:
    """G1–G8 with G2/G7/G8 at priority zero. Missing input alone needs input.

    G7 uses caller-supplied, index-aligned requirements and material metadata;
    raw wording is not reconstructed from a derived response or seed file.

    Evidence/value/design failures give not_biddable even if some inputs are
    also missing. Only an empty blocker list gives biddable. Row indexes are
    zero-based and allow locating inputs even when their identity is missing.
    biddable_rows counts L0/L1 evidence; passed_rows additionally requires no
    other blocker. NA rows are excluded from both and from evidence counts.
    """
    metadata = asdict(task) if isinstance(task, TaskMetadata) else dict(task)
    if requirements is not None and len(requirements) != len(rows):
        raise ValueError("requirements must align one-to-one with response rows")
    title = metadata.get("tender_package_name", "")
    identifiers = metadata.get("solidified_ids", [])
    valid_ids = isinstance(identifiers, (list, tuple)) and all(isinstance(i, str) and i.strip() for i in identifiers)
    id_set = set(identifiers) if valid_ids else set()
    patterns = [re.compile(p) for p in (forbidden_patterns if forbidden_patterns is not None else load_rules()["forbidden_patterns"])]
    blockers: list[Blocker] = []
    warnings: list[str] = []
    levels = {f"L{i}": 0 for i in range(5)}
    stats = {"total_rows": len(rows), "applicable_rows": 0, "biddable_rows": 0,
             "evidence_levels": levels, "key_non_L0_L1_count": 0,
             "design_confirmation_count": 0, "enterprise_required_count": 0,
             "na_count": 0, "skip_count": 0, "evidence_cannot_fix_count": 0}
    reason_rows: dict[str, set[int]] = {name: set() for name in (
        "key_parameter", "design_confirmation", "enterprise_required", "master_data", "placeholder", "derivation_skip",
        "material_mismatch", "key_na_response")}

    if not isinstance(title, str) or not title.strip() or not id_set:
        blockers.append(Blocker("G0", "", "", str(title or "任务元数据"),
                                "需补充招标包名和固化ID清单", 1, None))
    if not rows:
        blockers.append(Blocker("G0", "", "", str(title), "尚未提供参数行", 1, None))

    for index, row in enumerate(rows):
        if not isinstance(row, (Response, DerivationSkip)):
            raise TypeError(f"row {index} must be Response or DerivationSkip")
        material_mismatch = None
        if isinstance(row, Response) and requirements is not None and materials is not None:
            material_mismatch = _material_conflict(requirements[index], materials.get(row.solidified_id, {}))
        key_na_response = _key_negative_response(row)
        # A critical negative response/conflicting material cannot hide behind
        # an explicit NA flag. Ordinary NA and skipped NA remain excluded.
        if (row.operator == "na" or isinstance(row, DerivationSkip) and row.reason == "na") and not (key_na_response or material_mismatch):
            stats["na_count"] += 1
            continue
        stats["applicable_rows"] += 1
        enterprise_required = bool(row.bidder_supplied or row.operator == "bidder_supplied")
        stats["enterprise_required_count"] += enterprise_required
        fid = row.solidified_id

        def block(code: str, reason: str, priority: int = 1, *, evidence_cannot_fix: bool = False) -> None:
            blockers.append(Blocker(code, fid, row.seq, row.name, reason, priority, index, evidence_cannot_fix))

        level = getattr(row.evidence_level, "value", row.evidence_level) if isinstance(row, Response) else None
        verified = level in {"L0", "L1"}
        key_parameter = row.is_key and not verified
        design_confirmation = isinstance(row, Response) and "source=" in row.basis
        derivation_skip = isinstance(row, DerivationSkip)
        flags = {
            "key_parameter": key_parameter and not derivation_skip,
            "design_confirmation": design_confirmation,
            "enterprise_required": enterprise_required and not derivation_skip,
            "master_data": (not verified and not (key_parameter or design_confirmation or enterprise_required)
                            and level != "L4" and not derivation_skip),
            "placeholder": level == "L4",
            "derivation_skip": derivation_skip,
            "material_mismatch": bool(material_mismatch),
            "key_na_response": key_na_response,
        }
        for reason, present in flags.items():
            if present:
                reason_rows[reason].add(index)
        if key_parameter:
            stats["key_non_L0_L1_count"] += 1
        if isinstance(row, DerivationSkip):
            # A skip has no guaranteed response identity/evidence. Locate it by
            # row_index only; missing identity must not create a second G0.
            stats["skip_count"] += 1
            block("G6", f"推导未完成：{row.reason}；{row.detail}")
            continue
        if fid not in id_set:
            block("G0", "Response的固化ID不在任务清单中")
        if key_parameter:
            block("G2", "关键参数缺少L0/L1证据", 0)
        if material_mismatch:
            block("G7", material_mismatch, 0)
        if key_na_response:
            stats["evidence_cannot_fix_count"] += 1
            block("G8", "关键参数采用否定性应答；补检测报告不能解决，须改设计或列入技术偏差表并附说明", 0,
                  evidence_cannot_fix=True)
        if level in levels:
            levels[level] += 1
        else:
            stats["unknown_evidence_count"] = stats.get("unknown_evidence_count", 0) + 1
        if verified:
            stats["biddable_rows"] += 1
        else:
            block("G1", f"证据等级{level or '缺失'}不可投标，需L0/L1")
        if design_confirmation:
            stats["design_confirmation_count"] += 1
            block("G3", "需设计确认", 2)
        value = row.guarantee_value
        if value is None or not str(value).strip():
            block("G4", "保证值为空")
        elif any(pattern.fullmatch(str(value).strip()) for pattern in patterns):
            block("G4", "保证值为空洞应答")

    if rows and stats["applicable_rows"] == 0:
        blockers.append(Blocker("G0", "", "", str(title),
                                "无可适用行（全部标记为不适用）", 1, None))
    stats["blocking_reasons"] = {reason: len(indices) for reason, indices in reason_rows.items()}
    stats["blocking_reasons"]["union_rows"] = len(set().union(*reason_rows.values()))
    blockers.sort(key=lambda b: (b.priority, b.row_index if b.row_index is not None else -1, b.code))
    blocked_rows = {blocker.row_index for blocker in blockers if blocker.row_index is not None}
    stats["blocked_row_count"] = len(blocked_rows)
    stats["passed_rows"] = stats["applicable_rows"] - len(blocked_rows)
    stats["blocker_counts"] = dict(Counter(blocker.code for blocker in blockers))
    if stats["na_count"]:
        warnings.append(f"已排除{stats['na_count']}条明确不适用行")
    if not blockers:
        decision = "biddable"
    elif any(b.code in {"G1", "G2", "G3", "G4", "G7", "G8"} for b in blockers):
        decision = "not_biddable"
    else:
        decision = "needs_input"
    return GateResult(decision, blockers, warnings, stats)
