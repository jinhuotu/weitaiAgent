"""演示开关：开着只拷事先放好的稿，关着不读这份清单。"""

from __future__ import annotations

import json

import pytest

from common.config import get_settings
from common.errors import AppError


@pytest.fixture
def demo_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("TENDER_DEMO", "1")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _manifest(root, *, with_file: bool) -> None:
    case = root / "tender-demo" / "invite-a"
    case.mkdir(parents=True)
    (case / "business.docx").write_bytes(b"PK-demo-a")
    (root / "tender-demo" / "manifest.json").write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "demo0000a001",
                        "match": ["高新"],
                        "projectName": "郑州高新充电项目",
                        "tenderer": "郑州天健智慧园区运营管理有限公司",
                        "tenderNo": "26-GCZN-0150",
                        "bidPriceYuan": 5188000,
                        "business": "invite-a/business.docx",
                    },
                    {
                        "id": "demo0000b002",
                        "match": ["备件"],
                        "projectName": "第二份演示项目",
                        "tenderer": "第二份演示招标人",
                        "tenderNo": "DEMO-002",
                        "bidPriceYuan": 1000000,
                        "business": "invite-b/missing.docx",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    del with_file


@pytest.mark.asyncio
async def test_demo_parse_and_copy(demo_settings, tmp_path, monkeypatch) -> None:
    from api.services.tenders import demo
    from api.services.tenders.schema import BidBrief

    async def _noop(_seconds: float) -> None:
        return None

    monkeypatch.setattr(demo, "_pause", _noop)
    demo._picked.clear()
    _manifest(tmp_path, with_file=True)
    payload = await demo.demo_parse("郑州高新邀请书.pdf")
    assert payload["brief"]["projectName"] == "郑州高新充电项目"
    assert payload["brief"]["invitationId"] == "demo0000a001"
    assert payload["brief"]["bidPriceYuan"] == 5188000
    assert payload["brief"]["quoteLines"][0]["name"]
    assert "演示" not in "".join(payload["notes"])

    brief = BidBrief.model_validate(payload["brief"])
    out = await demo.build_demo_output(brief)
    assert str(out["docxFile"]).endswith(".docx")
    saved = tmp_path / "tenders" / str(out["docxFile"])
    assert saved.read_bytes() == b"PK-demo-a"


@pytest.mark.asyncio
async def test_demo_missing_file_does_not_fall_through(demo_settings, tmp_path, monkeypatch) -> None:
    from api.services.tenders import demo
    from api.services.tenders.schema import BidBrief

    async def _noop(_seconds: float) -> None:
        return None

    monkeypatch.setattr(demo, "_pause", _noop)
    demo._picked.clear()
    _manifest(tmp_path, with_file=True)
    payload = await demo.demo_parse("备件采购.pdf")
    brief = BidBrief.model_validate(payload["brief"])
    with pytest.raises(AppError, match="演示成稿还没放好"):
        await demo.build_demo_output(brief)


def test_demo_off(monkeypatch, tmp_path) -> None:
    from api.services.tenders.demo import demo_enabled

    monkeypatch.setenv("TENDER_DEMO", "0")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    try:
        assert demo_enabled() is False
    finally:
        get_settings.cache_clear()
