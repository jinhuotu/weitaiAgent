"""报价决策：多方案 + 风险 + 编制说明（数字用规则，文案模板）。"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

_TWO = Decimal("0.01")

_COMPETE = {
    "conservative": ("保守", Decimal("1.08"), Decimal("0.12")),
    "balanced": ("均衡", Decimal("1.00"), Decimal("0.08")),
    "aggressive": ("进取", Decimal("0.94"), Decimal("0.05")),
}


def _q(raw: object) -> Decimal:
    try:
        return Decimal(str(raw or "0"))
    except Exception:
        return Decimal("0")


def build_schemes(
    *,
    cost_ex: Decimal,
    quote_ex: Decimal,
    bid_ceiling: Decimal | None,
    competition: str,
    target_margin: Decimal | None,
    project_name: str,
    location: str,
    duration_days: float | None,
) -> tuple[list[dict[str, Any]], str, list[str]]:
    key = (competition or "balanced").strip().lower()
    if key not in _COMPETE:
        key = "balanced"
    label, coef, default_margin = _COMPETE[key]
    margin = target_margin if target_margin is not None and target_margin >= 0 else default_margin
    if margin > 1:
        margin = margin / Decimal("100")

    base = quote_ex if quote_ex > 0 else cost_ex
    if base <= 0:
        base = Decimal("0")

    schemes: list[dict[str, Any]] = []
    presets = [
        ("保守方案", Decimal("1.06"), "偏高报价，中标概率偏低，利润空间更大"),
        ("均衡方案", coef, f"按{label}档竞争系数编制"),
        ("进取方案", Decimal("0.92"), "偏低报价抢标，需严控成本与变更"),
    ]
    risks: list[str] = []
    for name, c, tip in presets:
        ex = (base * c).quantize(_TWO, rounding=ROUND_HALF_UP)
        if cost_ex > 0:
            m = ((ex - cost_ex) / ex).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) if ex > 0 else Decimal("0")
        else:
            m = margin
        # 若指定目标毛利，均衡方案锚定
        if name.startswith("均衡") and cost_ex > 0 and margin > 0:
            ex = (cost_ex / (Decimal("1") - margin)).quantize(_TWO, rounding=ROUND_HALF_UP)
            m = margin
        risk_flags: list[str] = []
        if bid_ceiling is not None and bid_ceiling > 0 and ex > bid_ceiling:
            risk_flags.append("超过招标限价")
            risks.append(f"{name}不含税 {float(ex):.2f} 超过限价 {float(bid_ceiling):.2f}")
        if cost_ex > 0 and ex < cost_ex:
            risk_flags.append("低于成本")
            risks.append(f"{name}低于测算成本")
        if m < Decimal("0.03"):
            risk_flags.append("毛利过低")
        schemes.append(
            {
                "name": name,
                "coef": float(c),
                "totalExTax": float(ex),
                "margin": float(m),
                "tip": tip,
                "risks": risk_flags,
                "recommended": name.startswith("均衡"),
            }
        )

    if bid_ceiling is not None and bid_ceiling > 0:
        room = bid_ceiling - cost_ex
        if room < 0:
            risks.append("测算成本已高于招标限价，需核减工程量或议价")
        else:
            risks.append(f"限价相对成本余量约 {float(room):.2f} 元")

    loc = (location or "").strip() or "未注明"
    days = f"{int(duration_days)} 天" if duration_days else "未注明"
    title = (project_name or "").strip() or "本项目"
    rec = next((s for s in schemes if s.get("recommended")), schemes[1] if len(schemes) > 1 else schemes[0])
    instruction = (
        f"「{title}」报价编制说明\n"
        f"一、编制依据：场地规划图/工程量清单识别结果，单价匹配所选知识库价目；"
        f"成本侧含材料、措施费、管理费等费率测算。\n"
        f"二、项目参数：地点 {loc}；工期 {days}；竞争策略 {label}。\n"
        f"三、推荐方案：{rec['name']}，不含税合计约 {rec['totalExTax']:.2f} 元，"
        f"测算毛利率约 {rec['margin'] * 100:.1f}%。\n"
        f"四、风险提示：{'；'.join(risks) if risks else '未见明显限价/成本冲突，仍请人工复核工程量与价目。'}\n"
        f"五、说明：本文件由系统辅助生成，投标前须由商务/造价人员审核签认。"
    )
    return schemes, instruction, risks
