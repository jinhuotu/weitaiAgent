"""投标生成记录序列化与文件校验（不依赖异步 DB 驱动）。"""

from __future__ import annotations

from datetime import datetime, timezone

from api.services.tenders.records import _record_to_dict
from db.models.tender import TenderRecord


def test_record_to_dict_marks_missing_docx(tmp_path, monkeypatch):
    from api.services.tenders import records as records_mod

    out = tmp_path / "tenders"
    out.mkdir()
    monkeypatch.setattr(records_mod, "tenders_output_dir", lambda: out)

    row = TenderRecord(
        id=1,
        public_id="abc123def4567890",
        user_id=1,
        username="admin",
        project_name="测试项目",
        tenderer="招标人",
        bid_price_yuan=1000,
        legal_person_name="张三",
        docx_file="aabbccddee01.docx",
        pdf_file=None,
        download_name="测试-投标文件.docx",
        pdf_download_name=None,
        warnings=["w1"],
        brief_json={"projectName": "测试项目"},
        created_at=datetime(2026, 8, 31, 1, 2, 3, tzinfo=timezone.utc),
    )
    data = _record_to_dict(row)
    assert data["id"] == "abc123def4567890"
    assert data["projectName"] == "测试项目"
    assert data["docxAvailable"] is False
    assert data["warnings"] == ["w1"]

    (out / "aabbccddee01.docx").write_bytes(b"PK")
    data2 = _record_to_dict(row, include_brief=True)
    assert data2["docxAvailable"] is True
    assert data2["brief"]["projectName"] == "测试项目"


def test_get_record_returns_when_file_missing(tmp_path, monkeypatch):
    import asyncio

    from api.services.tenders import records as records_mod

    out = tmp_path / "tenders"
    out.mkdir()
    monkeypatch.setattr(records_mod, "tenders_output_dir", lambda: out)

    class FakeResult:
        def scalar_one_or_none(self):
            return TenderRecord(
                public_id="deadbeefcafebabe",
                user_id=1,
                username="admin",
                project_name="丢失",
                tenderer="",
                bid_price_yuan=0,
                legal_person_name="",
                docx_file="missing000001.docx",
                download_name="x.docx",
                warnings=[],
                brief_json={"projectName": "丢失", "factoryRole": "投标产品生产厂商"},
            )

    class FakeDb:
        async def execute(self, *_args, **_kwargs):
            return FakeResult()

    async def run():
        data = await records_mod.get_record(FakeDb(), "deadbeefcafebabe")  # type: ignore[arg-type]
        assert data["docxAvailable"] is False
        assert data["brief"]["projectName"] == "丢失"
        assert data["brief"]["factoryRole"] == "投标产品生产厂商"

    asyncio.run(run())
