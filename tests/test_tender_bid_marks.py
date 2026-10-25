from types import SimpleNamespace

from api.services.menus import resolve_menus
from api.services.tenders.bid_marks import plan_marks


def _chapters():
    return [
        {"volume": "business", "title": "投标函"},
        {"volume": "technical", "title": "售后服务"},
    ]


def test_lost_marks_from_warnings_and_reject_comment():
    rows = plan_marks(
        status="lost",
        chapters=_chapters(),
        warnings=["未上传报价清单，未填写报价表", "生成耗时约 2s"],
        comments=["资质证书过期，驳回"],
        existing={},
    )
    assert {r["title"] for r in rows} == {"投标函", "售后服务"}
    assert all(r["verdict"] == "bad" for r in rows)
    assert rows[0]["problemTags"] == ["报价", "资质", "否决"]
    assert "其他" not in rows[0]["problemTags"]
    assert rows[0]["source"] == "auto"
    assert "资质证书过期" in rows[0]["note"]


def test_comment_without_keyword_is_other():
    rows = plan_marks(
        status="lost",
        chapters=[{"volume": "business", "title": "投标函"}],
        warnings=["生成耗时约 2s"],
        comments=["请重做封面"],
        existing={},
    )
    assert rows[0]["problemTags"] == ["其他"]
    assert rows[0]["source"] == "approval"
    assert rows[0]["note"] == "请重做封面"


def test_won_is_good_and_ignores_old_reject():
    rows = plan_marks(
        status="won",
        chapters=_chapters(),
        warnings=["未填写报价表"],
        comments=["驳回"],
        existing={},
    )
    assert len(rows) == 2
    assert all(r["verdict"] == "good" and r["problemTags"] == [] and r["note"] == "" for r in rows)
    assert rows[0]["source"] == "auto"


def test_manual_mark_not_overwritten():
    rows = plan_marks(
        status="lost",
        chapters=_chapters(),
        warnings=["未填写报价表"],
        comments=["驳回"],
        existing={("business", "投标函"): {"source": "manual", "verdict": "good"}},
    )
    assert [r["title"] for r in rows] == ["售后服务"]


def test_status_words_are_not_problem_notes():
    rows = plan_marks(
        status="lost",
        chapters=[{"volume": "business", "title": "投标函"}],
        warnings=[],
        comments=["lost", "won"],
        existing={},
    )
    assert rows[0]["verdict"] == "bad"
    assert rows[0]["problemTags"] == []
    assert rows[0]["note"] == ""
    assert rows[0]["source"] == "auto"


def test_approval_good_not_flipped_to_bad():
    rows = plan_marks(
        status="lost",
        chapters=[{"volume": "business", "title": "投标函"}],
        warnings=["未填写报价表"],
        comments=["驳回"],
        existing={("business", "投标函"): {"source": "approval", "verdict": "good"}},
    )
    assert rows == []


def test_processing_does_not_seed():
    assert plan_marks(status="processing", chapters=_chapters(), warnings=["报价"], comments=["驳回"]) == []


def test_operator_with_tenders_sees_history():
    user = SimpleNamespace(is_superuser=False, roles=[SimpleNamespace(code="operator")])
    menus = resolve_menus(user)
    assert "/history-bids" in menus
    assert "/tender-library" not in menus
