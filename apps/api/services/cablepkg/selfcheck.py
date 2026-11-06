"""Read-only seed -> semantics -> design -> gate -> deterministic Markdown.

Run with the project's apps directory on PYTHONPATH:
python -m api.services.cablepkg.selfcheck --seed INPUT --out REPORT
"""

import argparse
from dataclasses import dataclass, replace
from datetime import datetime, timezone, timedelta
import hashlib
import html
import json
from pathlib import Path
import re
from typing import Any

from .derive import DerivationSkip, derive
from .gate import GateResult, TaskMetadata, evaluate_gate
from .model import Requirement, Response
from .semantics import parse_requirement


@dataclass
class SelfcheckResult:
    task_name: str
    materials: list[dict[str, Any]]
    requirements: list[Requirement]
    outcomes: list[Response | DerivationSkip]
    gate: GateResult
    rules_sha256: str


def run_pipeline(seed: str | Path, *, task_name: str = "电缆参数包") -> SelfcheckResult:
    """Only the caller-selected input and the bundled rule snapshot are read."""
    data = json.loads(Path(seed).read_text(encoding="utf-8"))
    materials = data["materials"]
    by_id = {material["fid"]: material for material in materials}
    if len(by_id) != len(materials):
        raise ValueError("固化ID清单存在重复项")
    rule_bytes = Path(__file__).with_name("rules_cable.json").read_bytes()
    policy = json.loads(rule_bytes)
    requirements, outcomes = [], []
    for row in data["rows"]:
        req = replace(parse_requirement(row["requirement_raw"]),
                      solidified_id=row["solidified_id"], seq=str(row["seq"]),
                      name=row["name"], unit=row.get("unit", ""),
                      is_key=row.get("is_key", False), needs_report=row.get("needs_report", False))
        requirements.append(req)
        # Unknown material IDs become a located skip; they never disappear.
        if req.solidified_id not in by_id:
            outcome = DerivationSkip("invalid_input", "固化ID缺少物料元数据",
                                     solidified_id=req.solidified_id, seq=req.seq, name=req.name,
                                     is_key=req.is_key, bidder_supplied=req.bidder_supplied,
                                     operator=req.operator)
        else:
            outcome = derive(req, by_id[req.solidified_id], rules=policy)
        outcomes.append(outcome)
    gate = evaluate_gate(outcomes, TaskMetadata(task_name, list(by_id)),
                         forbidden_patterns=policy["forbidden_patterns"],
                         requirements=requirements, materials=by_id)
    return SelfcheckResult(task_name, materials, requirements, outcomes, gate,
                           hashlib.sha256(rule_bytes).hexdigest())


# Redact paths in *all* caller-provided fields, not just seed provenance.
_ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|(?<![\w])/(?=\S))[^\n|<>]*")


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    text = _ABSOLUTE_PATH.sub("招标包内文件", text)
    return html.escape(text, quote=False).replace("|", "&#124;").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def _table(headers, rows) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return lines + [""]


def _seq_key(value: str):
    return tuple(int(part) for part in re.findall(r"\d+", value)) or (0,)


def _basis(outcome: Response | DerivationSkip) -> str:
    if isinstance(outcome, DerivationSkip):
        return f"{outcome.reason}：{outcome.detail}"
    # Keep source and time base explicit while omitting machine-oriented params.
    parts = outcome.basis.split("; ")
    return "; ".join(part for part in parts if not part.startswith(("action=", "params="))).replace(
        "不代表已通过试验", "不作为试验结论")


def render_report(result: SelfcheckResult, *, no_timestamp: bool = False) -> str:
    """Render every input row; table cells never declare bidding clearance."""
    stats = result.gate.stats
    decision = {"not_biddable": "**不可投标**", "needs_input": "**待补充输入**",
                "biddable": "证据闸门无阻断（仍需人工核验）"}[result.gate.decision]
    lines = [f"# {_cell(result.task_name)} 自检报告", "", f"任务名：{_cell(result.task_name)}",
             f"决策：{decision}", f"可投 {stats['passed_rows']} / {stats['total_rows']}"]
    if not no_timestamp:
        now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
        lines.append(f"生成时间：{now}")
    lines += ["", "> **本报告基于示例数据生成，L0/L1 证据为 0，不代表任何企业真实能力，不得作为投标依据。**", "",
              "## 阻断总览", ""]
    reasons = stats["blocking_reasons"]
    categories = [
        ("关键参数", "key_parameter", "不可仅批量复制：逐项补充L0/L1证据。"),
        ("设计待确认", "design_confirmation", "可按结构系列批量复核，需企业结构图确认。"),
        ("企业资料待补", "enterprise_required", "可批量导入企业设计资料，须逐项核验证据。"),
        ("产品主数据待建档", "master_data", "可按产品系列批量建档，须提供L0/L1来源。"),
        ("组件材料待填", "placeholder", "可按供应商批量填报制造商、规格及原产地事实。"),
        ("推导未完成", "derivation_skip", "可按缺失输入或未命中规则归组修复，再重新自检。"),
        ("导体材料冲突", "material_mismatch", "先核对招标原文与物料清单，逐项修正冲突。"),
        ("关键参数否定性应答", "key_na_response", "补检测报告不能解决，须改设计或列入技术偏差表。"),
    ]
    lines += _table(["阻断类别", "行数", "可否批量解决"],
                    [(name, reasons[key], advice) for name, key, advice in categories]
                    + [("去重合计", reasons["union_rows"], "类别可重叠，合计按行去重；不得忽略非关键项。")])
    codes: dict[int, list[str]] = {}
    for blocker in result.gate.blockers:
        if blocker.row_index is not None:
            codes.setdefault(blocker.row_index, []).append(blocker.code)
    # Preserve material order. Ties in numerical sequence retain input order.
    id_order = {material["fid"]: index for index, material in enumerate(result.materials)}
    order = sorted(range(len(result.requirements)),
                   key=lambda i: (id_order.get(result.requirements[i].solidified_id, len(id_order)),
                                  _seq_key(result.requirements[i].seq), i))

    def values(index):
        req, out = result.requirements[index], result.outcomes[index]
        if isinstance(out, Response):
            value, level = out.guarantee_value, out.evidence_level.value
        else:
            value, level = "未生成", "—"
        return req, out, value, level

    def status(index):
        if codes.get(index):
            return "阻断：" + "、".join(codes[index])
        return "不适用（待核对）" if result.requirements[index].operator == "na" else "待人工核验"

    def checklist(indices):
        return _table(["固化ID", "序号", "参数", "单位", "招标要求", "承诺值", "等级", "状态"],
                      [(req.solidified_id, req.seq, req.name, req.unit, req.raw, value, level, status(index))
                       for index in indices for req, out, value, level in [values(index)]])

    lines += ["## 数据冲突与不可通过补证解决的问题", "",
              f"导体材料冲突：{reasons['material_mismatch']}行；关键参数否定性应答：{reasons['key_na_response']}行；"
              f"补证无法解决：{stats['evidence_cannot_fix_count']}行。G7、G8与G2同为最高优先级。", ""]
    conflicts = [b for b in result.gate.blockers if b.code in {"G7", "G8"}]
    conflicts.sort(key=lambda b: (order.index(b.row_index), b.code))
    by_id = {material["fid"]: material for material in result.materials}
    lines += _table(["固化ID", "序号", "参数", "招标要求", "物料导体", "承诺值", "阻断码", "原因", "处置建议"],
                    [(req.solidified_id, req.seq, req.name, req.raw,
                      by_id.get(req.solidified_id, {}).get("cond", "未提供"), value,
                      blocker.code, blocker.reason,
                      "改物料主数据" if blocker.code == "G7" else "列入技术偏差表并附说明")
                     for blocker in conflicts for req, out, value, level in [values(blocker.row_index)]])
    lines += ["## 关键参数专区", ""]
    lines += checklist([i for i in order if result.requirements[i].is_key])
    lines += ["## 按固化ID明细", ""]
    groups = [(material["fid"], material) for material in result.materials]
    # Retain malformed rows too, instead of silently dropping unknown IDs.
    groups += [(fid, None) for fid in dict.fromkeys(result.requirements[i].solidified_id for i in order)
               if fid not in id_order]
    for fid, material in groups:
        description = (f"{material['model']} {material['cores']}×{material['area']} {material['cond']}"
                       if material else "物料元数据缺失")
        lines += [f"### {_cell(fid)} · {_cell(description)}", ""]
        lines += _table(["序号", "参数", "单位", "招标要求", "承诺值", "等级", "阻断码", "依据摘要"],
                        [(req.seq, req.name, req.unit, req.raw, value, level,
                          "、".join(codes.get(i, [])) or status(i), _basis(out))
                         for i in order if result.requirements[i].solidified_id == fid
                         for req, out, value, level in [values(i)]])
    lines += ["## 待设计确认清单", ""]
    lines += checklist([i for i in order if isinstance(result.outcomes[i], Response)
                        and "source=" in result.outcomes[i].basis])
    lines += ["## 组件材料待填清单", ""]
    lines += checklist([i for i in order if isinstance(result.outcomes[i], Response)
                        and result.outcomes[i].evidence_level.value == "L4"])
    lines += ["## 方法与口径", "",
              "来源：招标包内文件。流程：逐行语义解析 → 取值推导 → 证据闸门；seed仅作只读回归输入。", "",
              "证据等级：L0为带报告编号及页码的真实检测报告；L1为企业型式试验或产品主数据；"
              "L2为标准限值或需求照录；L3为结构设计计算；L4为示例占位。仅L0/L1可提供投标证据，仍须核验全部阻断项。", "",
              "**承诺值为设计估算，非检测结果**。L2需求照录和L4占位同样不是企业事实。", "",
              f"rules_cable.json 的 SHA-256：`{result.rules_sha256}`", "",
              "语义解析覆盖：大于等于/不小于/≥、小于等于/不大于/≤、区间、正负公差、纯数值、枚举及文本、"
              "投标人提供、明确不适用及空值；括号适用条件与百分比限定词分离，名称标记独立保留。"
              "种子回归匹配不构成正确性证明，规则正确性另由独立标注用例验证。", "",
              f"设计待确认行数：{stats['design_confirmation_count']}；按依据中source=独立统计，不因与seed一致而豁免。", "",
              "阻断码：G0为任务/输入不完整，G1为证据不足，G2为关键参数证据不足，G3为需设计确认，"
              "G4为保证值为空或空洞，G6为推导未完成，G7为导体材料冲突，G8为关键参数否定性应答；"
              "G8标记evidence_cannot_fix=true，不能通过补检测报告消除。"
              "普通明确na行不参与证据统计，但G7/G8不得借na豁免；待填组件行不视为na。", "",
              "排序：固化ID沿用输入物料顺序；序号按各级数字升序，同序号沿用输入行顺序。", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="电缆参数包Markdown自检报告（只读种子）")
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--task-name", help="任务/招标包名；缺省取报告文件名去掉-selfcheck")
    parser.add_argument("--no-timestamp", action="store_true", help="省略生成时间，便于逐字节对拍")
    args = parser.parse_args(argv)
    if args.out.resolve().is_relative_to(args.seed.resolve().parent):
        parser.error("输出目录不得位于种子数据目录")
    try:
        result = run_pipeline(args.seed, task_name=args.task_name or args.out.stem.removesuffix("-selfcheck"))
        report = render_report(result, no_timestamp=args.no_timestamp)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(report.encode("utf-8"))
    except (OSError, KeyError, ValueError, TypeError):
        parser.exit(2, "自检失败：请检查输入结构、规则及输出权限。\n")
    print(f"自检报告已生成；decision={result.gate.decision}；total_rows={len(result.requirements)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
