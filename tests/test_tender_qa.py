"""投标生成后对照邀请书的质检：符合度与缺失项（不调真实模型）。"""

from __future__ import annotations

from api.services.tenders.qa import (
    SEVERITY_DISQUALIFY,
    build_report,
    combine_score,
    compact_text,
    grade_for,
    merge_gaps,
    parse_qa_payload,
    rule_inspect,
    title_in_text,
)
from api.services.tenders.schema import (
    BidBrief,
    DeviationLine,
    OutlineItem,
    PlaceholderItem,
    QuoteLineIn,
)


def test_title_in_text_compact_match() -> None:
    blob = "投标函\n法定代表人身份证明\n分项报价表"
    assert title_in_text(blob, "投标函")
    assert title_in_text(blob, "法定代表人 / 身份证明")
    assert not title_in_text(blob, "授权委托书")
    assert compact_text("法定代表人 / 身份证明") in compact_text(blob)


def test_rule_inspect_lists_missing_outline_and_quote() -> None:
    brief = BidBrief(
        projectName="某充电站项目",
        tenderer="某市热电有限公司",
        bidPriceYuan=518800,
        deliveryDays=30,
        outlineItems=[
            OutlineItem(id="o01", title="投标函", kind="letter", required=True),
            OutlineItem(id="o02", title="授权委托书", kind="auth", required=True),
            OutlineItem(id="o03", title="投标保证金", kind="scan", required=True, skipped=True),
        ],
        quoteLines=[
            QuoteLineIn(seq="1", name="交流充电桩", qty=10, unitPrice=1000, amount=10000),
            QuoteLineIn(seq="2", name="立柱", qty=10, unitPrice=100, amount=1000),
        ],
        requiredSlotKeys=["id_legal"],
        extraPlaceholders=[
            PlaceholderItem(key="id_legal", title="法定代表人身份证正反面"),
        ],
        deviationLines=[
            DeviationLine(
                seq="1",
                requirement="防护等级 IP54",
                response="响应",
                deviation="无偏差",
            ),
        ],
    )
    bid = """
    投标函
    项目名称：某充电站项目
    招标人：某市热电有限公司
    投标总价 518800 元
    供货期 30 天
    交流充电桩
    技术偏离表
    实施方案
    """
    result = rule_inspect(brief, bid, missing_slot_titles=["法定代表人身份证正反面"])
    titles = [g["title"] for g in result["missing"]]
    assert "授权委托书" in titles
    assert "立柱" in titles
    assert "法定代表人身份证正反面" in titles
    assert "投标函" not in titles
    assert "交流充电桩" not in titles
    assert any(
        g["severity"] == SEVERITY_DISQUALIFY and g["title"] == "授权委托书"
        for g in result["missing"]
    )
    assert result["coverage"]["outline"]["found"] == 1
    assert result["coverage"]["outline"]["total"] == 2
    assert result["score"] <= 72


def test_rule_inspect_complete_bid_scores_high() -> None:
    brief = BidBrief(
        projectName="园区充电桩",
        tenderer="甲方公司",
        bidPriceYuan=10000,
        deliveryDays=15,
        outlineItems=[OutlineItem(id="o01", title="投标函", kind="letter", required=True)],
        quoteLines=[QuoteLineIn(seq="1", name="直流桩", qty=1, unitPrice=10000, amount=10000)],
        deviationLines=[
            DeviationLine(seq="1", requirement="效率", response="响应", deviation="无偏差")
        ],
        requiredSlotKeys=[],
    )
    bid = "投标函 园区充电桩 甲方公司 10000 15天 直流桩 技术偏离表 实施方案"
    result = rule_inspect(brief, bid, missing_slot_titles=[])
    assert result["missing"] == []
    assert result["score"] >= 85


def test_parse_qa_payload_and_merge() -> None:
    payload = parse_qa_payload(
        '{"similarityScore": 77, "summary": "缺授权委托书",'
        ' "missing": [{"title": "授权委托书", "reason": "未见该节",'
        ' "severity": "disqualify", "category": "outline"},'
        ' {"title": "  ", "reason": "空"}]}'
    )
    assert payload["similarityScore"] == 77
    assert payload["missing"][0]["title"] == "授权委托书"
    merged = merge_gaps(
        [
            {
                "title": "授权委托书",
                "reason": "规则未找到",
                "severity": "deduct",
                "category": "outline",
                "source": "rule",
            }
        ],
        payload["missing"],
    )
    assert len(merged) == 1
    assert merged[0]["severity"] == SEVERITY_DISQUALIFY
    assert merged[0]["source"] == "rule"


def test_combine_score_and_grade() -> None:
    assert combine_score(80, None) == 80
    assert combine_score(80, 100) == 91
    assert grade_for(90) == "good"
    assert grade_for(75) == "fair"
    assert grade_for(40) == "risk"


def test_build_report_without_invitation() -> None:
    rule = {
        "score": 70,
        "coverage": {"outline": {"found": 1, "total": 2, "score": 50}},
        "missing": [
            {
                "title": "授权委托书",
                "reason": "未见",
                "severity": "disqualify",
                "category": "outline",
                "source": "rule",
            }
        ],
    }
    report = build_report(
        record_id="abc123def4567890",
        docx_file="aabbccddee01.docx",
        invitation="",
        bid_text="投标函",
        rule=rule,
        llm=None,
    )
    assert report["similarityScore"] == 70
    assert report["hasInvitation"] is False
    assert report["llmUsed"] is False
    assert report["grade"] == "fair"
    assert report["source"] == "generated"
    assert "未保存邀请书原文" in report["summary"]
    assert report["missing"][0]["title"] == "授权委托书"


def test_save_and_load_invitation_text(tmp_path, monkeypatch) -> None:
    from api.services.tenders import assets as assets_mod

    monkeypatch.setattr(assets_mod, "tender_invitations_dir", lambda: tmp_path)
    invite_id = assets_mod.save_invitation_text("投标邀请书正文内容" * 3, stem="aabbccddee01")
    assert invite_id == "aabbccddee01"
    assert assets_mod.load_invitation_text(invite_id).startswith("投标邀请书")
    assert assets_mod.load_invitation_text("no-such-id") == ""
    assert assets_mod.load_invitation_text("../etc/passwd") == ""


def test_build_report_upload_source() -> None:
    report = build_report(
        record_id="abc123def4567890",
        docx_file="aabbccddee01.docx",
        invitation="邀请书",
        bid_text="投标函",
        rule={"score": 88, "coverage": {}, "missing": []},
        llm=None,
        source="upload",
        upload_name="终稿-商务标.docx",
    )
    assert report["source"] == "upload"
    assert report["uploadName"] == "终稿-商务标.docx"
    assert report["similarityScore"] == 88


def test_save_qa_upload_and_summary(tmp_path, monkeypatch) -> None:
    from api.services.tenders import qa as qa_mod

    monkeypatch.setattr(qa_mod, "tenders_output_dir", lambda: tmp_path)
    pid = "aabbccddee01ff22"
    try:
        qa_mod.save_qa_upload(pid, b"not-a-docx")
        raise AssertionError("expected validation error")
    except Exception as exc:
        assert "docx" in str(exc).lower() or "Word" in str(exc)

    path = qa_mod.save_qa_upload(pid, b"PK" + b"\x00" * 80)
    assert path.name == f"{pid}.qa-upload.docx"
    assert path.is_file()

    qa_mod.save_qa_report(
        pid,
        {
            "similarityScore": 81,
            "grade": "fair",
            "source": "upload",
            "checkedAt": 1,
        },
    )
    summary = qa_mod.qa_summary(pid)
    assert summary["qaScore"] == 81
    assert summary["qaSource"] == "upload"

    qa_mod.unlink_qa_report(pid)
    assert not path.is_file()
    assert qa_mod.load_qa_report(pid) is None


def test_qa_reports_are_stored_per_volume(tmp_path, monkeypatch) -> None:
    from api.services.tenders import qa as qa_mod

    monkeypatch.setattr(qa_mod, "tenders_output_dir", lambda: tmp_path)
    pid = "aabbccddee01ff33"
    qa_mod.save_qa_report(pid, {"similarityScore": 68, "grade": "risk", "checkedAt": 10}, volume="business")
    qa_mod.save_qa_report(pid, {"similarityScore": 91, "grade": "good", "checkedAt": 20}, volume="technical")
    biz = qa_mod.load_qa_report(pid, volume="business")
    tech = qa_mod.load_qa_report(pid, volume="technical")
    assert biz is not None and biz["similarityScore"] == 68
    assert tech is not None and tech["similarityScore"] == 91
    latest = qa_mod.load_qa_report(pid)
    assert latest is not None and latest["similarityScore"] == 91
    qa_mod.unlink_qa_report(pid, volume="technical")
    assert qa_mod.load_qa_report(pid, volume="technical") is None
    assert qa_mod.load_qa_report(pid, volume="business")["similarityScore"] == 68


def test_legacy_qa_file_reads_as_business(tmp_path, monkeypatch) -> None:
    from api.services.tenders import qa as qa_mod

    monkeypatch.setattr(qa_mod, "tenders_output_dir", lambda: tmp_path)
    pid = "aabbccddee01ff44"
    path = qa_mod.qa_report_path(pid)
    path.write_text('{"similarityScore": 55, "grade": "risk"}', encoding="utf-8")
    hit = qa_mod.load_qa_report(pid, volume="business")
    assert hit is not None and hit["similarityScore"] == 55
    assert qa_mod.load_qa_report(pid, volume="technical") is None


def test_rule_inspect_technical_skips_quote_and_letter() -> None:
    brief = BidBrief(
        projectName="某充电站项目",
        tenderer="某市热电有限公司",
        bidPriceYuan=518800,
        quoteLines=[QuoteLineIn(seq="1", name="立柱", qty=10, unitPrice=100, amount=1000)],
        outlineItems=[
            OutlineItem(id="o01", title="投标函", kind="letter", required=True),
            OutlineItem(id="o02", title="技术标（实施方案）", kind="tech_plan", required=True),
        ],
        deviationLines=[
            DeviationLine(seq="1", requirement="防护等级", response="响应", deviation="无偏差"),
        ],
    )
    bid = "某充电站项目 某市热电有限公司 技术标 实施方案 技术偏差表 技术偏离表"
    result = rule_inspect(brief, bid, volume="technical")
    titles = [g["title"] for g in result["missing"]]
    assert "立柱" not in titles
    assert "投标函" not in titles
    assert result["coverage"]["quote"]["total"] == 0
