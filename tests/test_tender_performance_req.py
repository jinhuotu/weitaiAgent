"""招标书业绩门槛抽取与对照（合成正文，不依赖真实招标书）。"""

from __future__ import annotations

from api.services.tenders.performance import (
    extract_performance_requirement,
    format_requirement,
    match_performance_line,
    merge_performance_requirement,
    performance_match_issues,
    rank_performance_lines,
    requirement_from_payload,
)
from api.services.tenders.schema import PerformanceLine, PerformanceRequirement


def test_extract_charger_threshold() -> None:
    text = """
第三章 资格审查
投标人须提供近三年完成的类似充电桩项目不少于3个，单份合同金额不少于20万元，须已竣工验收。
投标保证金人民币伍万元整。
"""
    req = extract_performance_requirement(text)
    assert req.minAmountYuan == 200000
    assert req.minCount == 3
    assert req.requireCompleted is True
    assert "充电桩" in req.keywords
    assert "保证金" not in (req.note or "") or req.minAmountYuan != 50000


def test_extract_does_not_default_twenty_wan() -> None:
    text = "本项目交货期30天。投标有效期90天。质量要求合格。投标保证金5万元。"
    req = extract_performance_requirement(text)
    assert req.minAmountYuan == 0
    assert req.minCount == 0
    assert req.keywords == []


def test_extract_thermal_scope_not_charger() -> None:
    text = "资格条件：提供近三年类似热力项目不少于2个，单份金额不低于50万元。"
    req = extract_performance_requirement(text)
    assert req.minAmountYuan == 500000
    assert req.minCount == 2
    assert "热力" in req.keywords
    assert "充电桩" not in req.keywords


def test_bond_not_used_as_performance_amount() -> None:
    text = "投标保证金为人民币100万元。投标人应具有类似项目业绩。"
    req = extract_performance_requirement(text)
    assert req.minAmountYuan == 0 or req.minAmountYuan != 1000000


def test_match_amount_and_type_and_ongoing() -> None:
    req = PerformanceRequirement(
        similarScope="充电桩",
        keywords=["充电桩"],
        minAmountYuan=200000,
        minCount=2,
        requireCompleted=True,
    )
    ok = PerformanceLine(
        projectName="某站充电桩供货",
        chargerRelated=True,
        amountYuan=500000,
        ongoing=False,
    )
    low = PerformanceLine(
        projectName="充电桩小单",
        chargerRelated=True,
        amountYuan=80000,
        ongoing=False,
    )
    other = PerformanceLine(
        projectName="电缆敷设合同",
        amountYuan=800000,
        ongoing=False,
    )
    building = PerformanceLine(
        projectName="在建充电站",
        chargerRelated=True,
        amountYuan=900000,
        ongoing=True,
    )
    assert match_performance_line(ok, req).passed
    assert match_performance_line(low, req).reasons == ["金额不足"]
    assert "类型不符" in match_performance_line(other, req).reasons
    assert "在建" in match_performance_line(building, req).reasons


def test_match_inactive_requirement_passes() -> None:
    line = PerformanceLine(projectName="任意合同", amountYuan=1)
    assert match_performance_line(line, PerformanceRequirement()).passed


def test_rank_puts_matching_first() -> None:
    req = PerformanceRequirement(keywords=["充电桩"], minAmountYuan=200000)
    ranked = rank_performance_lines(
        [
            PerformanceLine(projectName="电缆大额", amountYuan=900000, ongoing=False),
            PerformanceLine(
                projectName="充电桩达标",
                chargerRelated=True,
                amountYuan=300000,
                ongoing=False,
            ),
        ],
        requirement=req,
    )
    assert ranked[0].projectName == "充电桩达标"


def test_merge_llm_amount_only_if_mentioned() -> None:
    rule = PerformanceRequirement()
    llm = PerformanceRequirement(minAmountYuan=200000, similarScope="充电桩", keywords=["充电桩"])
    merged = merge_performance_requirement(rule, llm, invitation="本项目交货期三十天")
    assert merged.minAmountYuan == 0
    trusted = merge_performance_requirement(
        rule, llm, invitation="类似项目单份不少于20万元"
    )
    assert trusted.minAmountYuan == 200000


def test_requirement_from_payload_and_format() -> None:
    req = requirement_from_payload(
        {
            "performanceRequirement": {
                "similarScope": "信息化系统",
                "minAmountYuan": 800000,
                "minCount": 2,
                "requireCompleted": True,
            }
        }
    )
    assert req.minCount == 2
    label = format_requirement(req)
    assert "80万元" in label
    assert "至少2个" in label


def test_performance_match_issues_count() -> None:
    req = PerformanceRequirement(keywords=["充电桩"], minAmountYuan=200000, minCount=3)
    lines = [
        PerformanceLine(projectName="充电A", chargerRelated=True, amountYuan=300000),
        PerformanceLine(projectName="充电B", chargerRelated=True, amountYuan=250000),
        PerformanceLine(projectName="电缆", amountYuan=900000),
    ]
    notes = performance_match_issues(lines, req)
    assert notes and "符合招标要求 2 条" in notes[0]
    assert "至少 3 个" in notes[0]


def test_merge_preselects_matching_lines() -> None:
    from api.services.tenders.extract import merge_performance_lines

    req = PerformanceRequirement(keywords=["充电桩"], minAmountYuan=200000, minCount=1)
    merged = merge_performance_lines(
        [],
        [
            PerformanceLine(projectName="充电桩达标", chargerRelated=True, amountYuan=300000),
            PerformanceLine(projectName="电缆大额", amountYuan=900000),
        ],
        requirement=req,
    )
    by_name = {item.projectName: item.includeInBid for item in merged}
    assert by_name["充电桩达标"] is True
    assert by_name["电缆大额"] is False


def test_merge_preserves_user_unchecked() -> None:
    from api.services.tenders.extract import merge_performance_lines

    req = PerformanceRequirement(keywords=["充电桩"], minAmountYuan=200000)
    merged = merge_performance_lines(
        [
            PerformanceLine(
                projectName="充电桩达标",
                chargerRelated=True,
                amountYuan=300000,
                includeInBid=False,
            )
        ],
        [
            PerformanceLine(projectName="充电桩达标", chargerRelated=True, amountYuan=300000),
            PerformanceLine(projectName="充电桩新单", chargerRelated=True, amountYuan=400000),
        ],
        requirement=req,
        preserve_flags=True,
    )
    by_name = {item.projectName: item.includeInBid for item in merged}
    assert by_name["充电桩达标"] is False
    assert by_name["充电桩新单"] is True


def test_match_issues_use_selected_rows() -> None:
    req = PerformanceRequirement(keywords=["充电桩"], minAmountYuan=200000, minCount=2)
    lines = [
        PerformanceLine(projectName="充电A", chargerRelated=True, amountYuan=300000, includeInBid=True),
        PerformanceLine(projectName="充电B", chargerRelated=True, amountYuan=250000, includeInBid=False),
    ]
    notes = performance_match_issues(lines, req)
    assert notes and "符合招标要求 1 条" in notes[0]


def test_extract_mes_mom_threshold() -> None:
    text = """
项目名称：数智化工厂MOM系统
资格条件：投标人须提供近三年类似制造执行/MES、MOM项目业绩。
"""
    req = extract_performance_requirement(text)
    assert req.minCount >= 3
    assert any(k.upper() in {"MES", "MOM"} for k in req.keywords)
    assert req.minAmountYuan == 0


def test_extract_ignores_mes_in_bid_content() -> None:
    text = """
项目名称：数智化工厂MOM系统
投标内容：含设备采买、TPM、WMS、MES、QMS模块及系统拓展。交货期30天。
"""
    req = extract_performance_requirement(text)
    assert req.keywords == []
    assert req.minCount == 0
    assert not req.similarScope


def test_match_mes_case_insensitive() -> None:
    req = PerformanceRequirement(keywords=["MES", "MOM"], minCount=3)
    ok = PerformanceLine(projectName="某厂 mes 系统实施", amountYuan=100)
    other = PerformanceLine(projectName="充电桩供货", chargerRelated=True, amountYuan=800000)
    assert match_performance_line(ok, req).passed
    assert "类型不符" in match_performance_line(other, req).reasons
