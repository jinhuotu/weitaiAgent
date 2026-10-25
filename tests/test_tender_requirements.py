"""否决项抽取、资料对账、质检覆盖。不拦下载。"""

from api.services.tenders.classifier import (
    bind_requirements,
    evidence_coverage,
    extract_requirements,
    extract_veto_requirements,
    merge_veto_requirements,
)
from api.services.tenders.qa import build_report
from api.services.tenders.schema import (
    BidBrief,
    BidRequirement,
    DeviationLine,
    OutlineItem,
    PerformanceLine,
    PlaceholderItem,
)


def test_veto_marks_skip_must_and_score_wording() -> None:
    text = "\n".join(
        [
            "第二章 投标人须知",
            "投标人必须在交易平台注册。",
            "业绩不全的不得分。",
            "投标人不得提供虚假证明材料，否则按废标处理。",
            "★ 投标人须具备有效营业执照，否则否决投标。",
        ]
    )
    items = extract_veto_requirements(text)
    blob = "".join(item.text for item in items)
    assert "交易平台" not in blob
    assert "不得分" not in blob
    assert "虚假" in blob
    assert "营业执照" in blob
    assert all(item.strength == "mandatory" for item in items)
    assert all(item.category == "veto" for item in items)
    assert items[0].sourceChapter.startswith("第二章")


def test_merge_keeps_scoring_and_upgrades_same_sentence() -> None:
    base = extract_requirements("实施方案具有针对性，符合评分标准。")
    assert base and base[0].category == "scoring"
    same = "投标人不得提供虚假证明材料，否则按废标处理。"
    base.append(BidRequirement(id="r99", text=same.replace(" ", ""), category="notice"))
    merged = merge_veto_requirements(base, extract_veto_requirements(same))
    scoring = next(item for item in merged if item.category == "scoring")
    assert scoring.strength == "general"
    veto = [item for item in merged if "虚假" in item.text]
    assert len(veto) == 1
    assert veto[0].strength == "mandatory"
    assert veto[0].category == "notice"
    assert veto[0].sourceChapter == ""


def test_bind_license_by_file_count_not_by_quoted_sentence() -> None:
    item = extract_veto_requirements("投标人须具备有效营业执照，否则否决投标。")[0]
    catalog = [PlaceholderItem(key="license", title="营业执照")]
    brief = BidBrief(
        layoutMode="outline",
        bidRequirements=[item],
        outlineItems=[OutlineItem(id="s", title="营业执照", kind="scan")],
    )
    empty = evidence_coverage(brief, catalog=catalog, file_counts={"license": 0})
    assert empty["redLine"] is False
    assert empty["mandatoryCovered"] == 0
    assert empty["mandatoryTotal"] == 1
    assert empty["rows"][0]["matchStatus"] == "gap"

    # 稿里复述招标原句也不算覆盖，这里根本不读正文。
    filled = evidence_coverage(brief, catalog=catalog, file_counts={"license": 2})
    assert filled["mandatoryCovered"] == 1
    assert filled["rows"][0]["matchStatus"] == "matched"
    assert filled["rows"][0]["slotKey"] == "license"


def test_tech_standard_stays_out_of_business_chapter() -> None:
    text = "\n".join(
        [
            "第二章 投标人须知",
            "“技术标准和要求”中的各项技术标准应当符合国家强制性标准，不得要求或标明某一特定的专利。",
            "投标人须具备有效营业执照，否则否决投标。",
        ]
    )
    items = extract_veto_requirements(text)
    tech = next(item for item in items if "技术标准" in item.text)
    lic = next(item for item in items if "营业执照" in item.text)
    assert tech.volume == "technical"
    assert lic.volume == "business"


def test_volumes_do_not_share_coverage() -> None:
    text = "\n".join(
        [
            "第二章 投标人须知",
            "投标人须具备有效营业执照，否则否决投标。",
            "投标文件未密封的，按无效投标处理。",
            "第三章 技术需求",
            "技术指标不得出现负偏离，否则否决投标。",
            "投标文件须按暗标要求编制，否则无效投标。",
            "商务条款不得擅自改变付款方式，否则按商务偏离处理。",
        ]
    )
    items = extract_veto_requirements(text)
    by_text = {item.text: item for item in items}
    license = next(item for item in items if "营业执照" in item.text)
    sealed = next(item for item in items if "密封" in item.text)
    tech = next(item for item in items if "技术指标" in item.text)
    blind = next(item for item in items if "暗标" in item.text)
    biz = next(item for item in items if "付款" in item.text)
    assert license.volume == "business"
    assert sealed.volume == "business"
    assert sealed.targetKind == "format"
    assert tech.volume == "technical"
    assert tech.targetKind == "tech_dev"
    assert blind.volume == "technical"
    assert biz.volume == "business"
    assert biz.targetKind == "biz_dev"
    assert by_text

    brief = BidBrief(
        layoutMode="outline",
        bidRequirements=items,
        outlineItems=[
            OutlineItem(id="p", title="技术偏离表", kind="tech_dev"),
            OutlineItem(id="b", title="商务偏离表", kind="biz_dev"),
        ],
        deviationLines=[DeviationLine(requirement="输出电压", response="满足")],
        deliveryDays=30,
    )
    technical = evidence_coverage(brief, volume="technical")
    business = evidence_coverage(brief, volume="business")
    tech_ids = {row["text"] for row in technical["rows"]}
    biz_ids = {row["text"] for row in business["rows"]}
    assert all(row["volume"] == "technical" for row in technical["rows"])
    assert all(row["volume"] == "business" for row in business["rows"])
    assert not tech_ids & biz_ids
    assert any("技术指标" in text for text in tech_ids)
    assert any("营业执照" in text for text in biz_ids)
    assert any("密封" in text for text in biz_ids)
    assert not any("密封" in text for text in tech_ids)
    tech_row = next(row for row in technical["rows"] if "技术指标" in row["text"])
    assert tech_row["matchStatus"] == "matched"
    # 只有技术偏离表、没有商务偏离的填写来源时，商务偏离不能被技术表覆盖。
    biz_only_tech_table = evidence_coverage(
        BidBrief(
            layoutMode="outline",
            bidRequirements=[biz],
            outlineItems=[OutlineItem(id="p", title="技术偏离表", kind="tech_dev")],
            deviationLines=[DeviationLine(requirement="输出电压", response="满足")],
            deliveryDays=30,
        ),
        volume="business",
    )
    assert biz_only_tech_table["rows"][0]["matchStatus"] == "unbound"
    assert biz_only_tech_table["mandatoryCovered"] == 0


def test_performance_and_deviation_need_a_table() -> None:
    perf = BidRequirement(
        id="v1",
        text="投标人不得没有类似业绩，否则否决投标。",
        category="veto",
        strength="mandatory",
        targetKind="performance",
    )
    dev = BidRequirement(
        id="v2",
        text="技术指标不得出现负偏离，否则否决投标。",
        category="veto",
        strength="mandatory",
        targetKind="deviation",
    )
    lines = [PerformanceLine(projectName="某充电站", includeInBid=True)]
    deviation = [DeviationLine(requirement="输出电压", response="满足")]
    with_tables = bind_requirements(
        [perf, dev],
        outline_items=[
            OutlineItem(id="p", title="类似业绩", kind="performance"),
            OutlineItem(id="d", title="技术偏离表", kind="tech_dev"),
        ],
        performance_lines=lines,
        deviation_lines=deviation,
        layout_mode="outline",
    )
    assert [item.matchStatus for item in with_tables] == ["matched", "matched"]

    no_tables = bind_requirements(
        [perf, dev],
        outline_items=[],
        performance_lines=lines,
        deviation_lines=deviation,
        layout_mode="outline",
    )
    assert [item.matchStatus for item in no_tables] == ["unbound", "unbound"]


def test_unmatched_qual_veto_notes_other_materials() -> None:
    item = BidRequirement(
        text="未按要求提供资质证明的，否决其投标。",
        strength="mandatory",
        category="veto",
    )
    bound = bind_requirements([item], catalog=[], file_counts={})
    assert bound[0].matchStatus == "unbound"
    assert "其他资料" in bound[0].matchNote


def test_format_clause_is_not_in_the_coverage_total() -> None:
    items = extract_veto_requirements("投标文件未密封的，按无效投标处理。")
    assert items[0].targetKind == "format"
    cov = evidence_coverage(
        BidBrief(layoutMode="outline", bidRequirements=items),
    )
    assert cov["formatCount"] == 1
    assert cov["mandatoryTotal"] == 0
    assert cov["mandatoryCovered"] == 0


def test_qa_report_shows_coverage_without_changing_score() -> None:
    item = extract_veto_requirements("投标人不得提供虚假证明材料，否则按废标处理。")[0]
    brief = BidBrief(bidRequirements=[item], layoutMode="outline")
    quoted = f"针对要求「{item.text}」我公司已响应。"
    report = build_report(
        record_id="abc123def4567890",
        docx_file="aabbccddee01.docx",
        invitation="邀请书里写了不得虚假",
        bid_text=quoted,
        rule={"score": 80, "coverage": {}, "missing": []},
        llm=None,
        brief=brief,
        file_counts={},
    )
    assert report["similarityScore"] == 80
    cov = report["requirementCoverage"]
    assert cov["redLine"] is False
    assert cov["mandatoryCovered"] == 0
    assert cov["mandatoryTotal"] == 1
    assert cov["coverageRate"] == 0
    recon = report["requirementReconcile"]
    assert recon["passed"] is False
    assert recon["gaps"] and recon["gaps"][0]["text"]
