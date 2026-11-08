"""Deterministic narrative route for cable technical scoring sub-items."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Sequence

from .chapter_plan import bigram_similarity

DEFAULT_SUBITEMS = (
    "设计质量保证体系及措施", "风险防范措施", "重要设计节点安排", "设计进度安排",
    "限额设计", "对设计意图和重难点的理解", "新技术新材料应用",
)
_FORBIDDEN = ("对应" + " ", "所需" + "材料：", "评分标准" + "进行编制")
_NAME_HINT = re.compile(r"(?:公司|集团|姓名|先生|女士|资质|证书|B证|工程师|项目经理)")


@dataclass(frozen=True)
class NarrativeSection:
    title: str
    body: str
    score: float | None = None


def _facts_text(facts: Mapping[str, Any]) -> str:
    values = []
    for key, value in facts.items():
        if value is None or value == "":
            continue
        text = str(value).strip()
        if _NAME_HINT.search(text):
            raise ValueError(f"facts 含不允许的人名/公司名/资质信息：{key}")
        values.append(f"{key}为{text}")
    return "；".join(values)


def _subitem(row: Any, index: int) -> tuple[str, float | None]:
    if isinstance(row, Mapping):
        return (str(row.get("title") or row.get("name") or row.get("requirement") or "").strip(),
                row.get("score", row.get("max_score")))
    return str(getattr(row, "title", getattr(row, "requirement", row))).strip(), getattr(row, "score", None)


def _body(title: str, facts: str, design: Mapping[str, Any], index: int) -> str:
    design_text = _facts_text(design) if design else "设计阶段、设计周期和设计标准按已核实资料执行"
    context = facts or "工程范围、工期、质量、安全和地点按招标文件及已核实事实执行"
    facts_piece = (facts + "。") if index == 0 else (design_text + "。") if index == 1 else "已核实的工程边界和现场条件作为输入。"
    openings = (
        f"围绕{title}，建立输入审查、专业会签、校核确认与成果交付闭环；每份文件保留版本轨迹，意见按责任界面归档。{facts_piece}通过抽查和复核确认结论，发现偏差立即回退到对应环节修正。",
        f"{title}采用风险清单管理，先辨识接口冲突、条件变化和实施约束，再分别设置预防、监测及应急动作；风险责任落实到工作包。{facts_piece}评审结论形成可追踪的关闭记录，避免同类问题重复发生。",
        f"针对{title}，把方案拆成勘察输入、专业计算、联合校审和成果确认四类节点，节点完成后才允许进入下一阶段。{facts_piece}图纸、计算书和说明文件同步更新，确保变更有来源、有影响分析。",
        f"在{title}安排上，按照先条件核对、再方案深化、后成果审查的顺序排布工作；以里程碑控制关键路径，以滚动计划吸收现场变化。{facts_piece}每次复盘记录完成量、未决项和下一步动作，保证计划可执行。",
        f"落实{title}时，先明确功能边界和投资约束，再比较不同技术路线的初期投入、运行维护与施工影响；选定方案后锁定控制指标。{facts_piece}设计调整须说明节约来源和质量影响，防止局部优化破坏整体目标。",
        f"对{title}的理解，重点在需求目标、现场条件、系统接口和运维便利之间取得平衡；先找出重难点，再用验证性计算和专题评审确定路径。{facts_piece}成果以可施工、可验收、可维护为检验尺度，确保设计意图能够落地。",
        f"{title}坚持适用性优先，围绕材料性能、工艺成熟度和环境适应性开展技术筛选；通过小范围验证、效果记录和交底培训控制应用风险。{facts_piece}新做法只有在指标可测、责任清楚、后续可维护时才纳入成果文件。",
    )
    pads = (
        "审查重点放在依据完整性、接口一致性和成果可追溯性，形成问题台账后按时复核。",
        "控制重点放在风险等级、触发条件和处置时限，必要时组织专题会并保留结论。",
        "节点成果须标明输入、输出和验收口径，相关专业在交付前完成联合签认。",
        "计划执行以实际完成情况为准，任何调整均记录原因、影响范围及恢复措施。",
        "经济性比较保留测算边界和采用理由，避免只看单项价格而忽略全寿命影响。",
        "难点处理坚持先验证后固化，重要假设在交底中说明，并在后续检查中回看。",
        "应用成果同步安排质量检查和运行反馈，确认效果后再形成可复制的技术做法。",
    )
    endings = (
        "交付前由专人核对目录、签批和版本状态。", "处置完成后复核残余风险并更新清单。",
        "节点关闭以文件齐全和意见销项为准。", "偏差超过控制线时立即启动纠偏程序。",
        "比较结论纳入审查记录并接受后续抽查。", "重点结论在交底后通过现场反馈验证。",
        "应用效果以检查记录和反馈结果共同确认。",
    )
    checks = (
        "必要时开展抽样复核。", "必要时开展压力复盘。", "必要时开展接口复核。",
        "必要时开展节奏复盘。", "必要时开展成本复核。", "必要时开展专题复盘。",
        "必要时开展效果复核。",
    )
    text = openings[index % len(openings)] + pads[index % len(pads)] + endings[index % len(endings)] + checks[index % len(checks)]
    return text


def generate_narrative_sections(
    subitems: Sequence[Any],
    facts: Mapping[str, Any],
    design_facts: Mapping[str, Any] | None = None,
) -> tuple[NarrativeSection, ...]:
    """Generate one factual, differentiated section per scoring sub-item."""
    rows = list(subitems) or [{"title": title} for title in DEFAULT_SUBITEMS]
    fact_text = _facts_text(facts)
    sections = []
    for index, row in enumerate(rows):
        title, score = _subitem(row, index)
        if not title:
            raise ValueError(f"评分子项 {index + 1} 缺少原名")
        body = _body(title, fact_text, design_facts or {}, index)
        if any(token in body for token in _FORBIDDEN):
            raise ValueError(f"正文含禁止说明句：{title}")
        sections.append(NarrativeSection(title, body, float(score) if score not in (None, "") else None))
    return tuple(sections)


def narrative_acceptance_report(sections: Sequence[NarrativeSection], before_chars: int = 215) -> dict[str, Any]:
    bodies = [section.body for section in sections]
    matrix = [[0.0 if i == j else bigram_similarity(a, b) for j, b in enumerate(bodies)]
              for i, a in enumerate(bodies)]
    forbidden = [token for token in _FORBIDDEN if any(token in body for body in bodies)]
    return {
        "I1": {"before": before_chars, "after": sum(map(len, bodies)), "ok": sum(map(len, bodies)) >= 1200},
        "I2": [{"title": s.title, "chars": len(s.body), "first_sentence": re.split(r"[。！？]", s.body)[0]}
               for s in sections],
        "I3": {"matrix": matrix, "max_overlap": max((max(row) for row in matrix), default=0.0),
               "ok": all(value < 0.45 for row in matrix for value in row)},
        "I4": {"hits": forbidden, "ok": not forbidden},
    }


def compare_design_facts(technical_facts: Mapping[str, Any], business_facts: Mapping[str, Any]) -> dict[str, Any]:
    """Compare three shared facts without allowing either volume to invent values."""
    keys = ("工程概况", "工期", "质量")
    rows = [{"fact": key, "technical": technical_facts.get(key), "business": business_facts.get(key),
             "match": technical_facts.get(key) == business_facts.get(key)} for key in keys]
    return {"rows": rows, "ok": all(row["match"] for row in rows)}


def compare_table_structure(original_sections: Mapping[str, Sequence[Any]], generated_sections: Mapping[str, Sequence[Any]]) -> dict[str, Any]:
    """Require tables only where the original technical format has them."""
    original = {key for key, tables in original_sections.items() if tables}
    generated = {key for key, tables in generated_sections.items() if tables}
    return {"original": sorted(original), "generated": sorted(generated),
            "missing": sorted(original - generated), "extra": sorted(generated - original),
            "ok": original <= generated and generated <= original}
