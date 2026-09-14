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
    assert data2["status"] == "processing"
    assert data2["workflowLocked"] is False


def test_get_record_returns_when_file_missing(tmp_path, monkeypatch):
    import asyncio

    from api.services.tenders import records as records_mod

    out = tmp_path / "tenders"
    out.mkdir()
    monkeypatch.setattr(records_mod, "tenders_output_dir", lambda: out)

    class FakeResult:
        def scalar_one_or_none(self):
            return TenderRecord(
                id=1,
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

        def scalars(self):
            return self

        def all(self):
            return []

    class FakeDb:
        async def execute(self, *_args, **_kwargs):
            return FakeResult()

    async def run():
        data = await records_mod.get_record(FakeDb(), "deadbeefcafebabe")  # type: ignore[arg-type]
        assert data["docxAvailable"] is False
        assert data["brief"]["projectName"] == "丢失"
        assert data["brief"]["factoryRole"] == "投标产品生产厂商"

    asyncio.run(run())


def test_infer_project_type_and_pass_step():
    from api.services.tenders.records import (
        STATUS_APPROVED,
        STATUS_PENDING,
        infer_project_type,
        next_step_on_pass,
        workflow_locked,
    )

    assert infer_project_type("武汉光谷数据中心机房") == "数据中心"
    assert infer_project_type("管廊监控") == "安防系统"
    assert infer_project_type("智慧园区") == "智慧园区"
    assert infer_project_type("弱电工程") == "弱电工程"
    assert next_step_on_pass("部门经理审批") == (STATUS_PENDING, "review_gm")
    assert next_step_on_pass("财务审核") == (STATUS_APPROVED, "done")
    assert workflow_locked("pending") is True
    assert workflow_locked("processing") is False


def test_clear_mine_records_admin_deletes_locked(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from api.services.tenders import records as records_mod

    out = tmp_path / "tenders"
    out.mkdir()
    (out / "keepme00000001.docx").write_bytes(b"PK")
    (out / "locked00000001.docx").write_bytes(b"PK")
    monkeypatch.setattr(records_mod, "tenders_output_dir", lambda: out)

    processing = TenderRecord(
        id=1,
        public_id="proc000000000001",
        user_id=1,
        username="admin",
        project_name="编制中",
        tenderer="",
        bid_price_yuan=0,
        legal_person_name="",
        docx_file="keepme00000001.docx",
        download_name="a.docx",
        warnings=[],
        brief_json={},
        status="processing",
    )
    pending = TenderRecord(
        id=2,
        public_id="pend000000000001",
        user_id=1,
        username="admin",
        project_name="待审",
        tenderer="",
        bid_price_yuan=0,
        legal_person_name="",
        docx_file="locked00000001.docx",
        download_name="b.docx",
        warnings=[],
        brief_json={},
        status="pending",
    )
    deleted: list[object] = []

    class FakeResult:
        def scalars(self):
            return self

        def all(self):
            return [processing, pending]

    class FakeDb:
        async def execute(self, *_args, **_kwargs):
            return FakeResult()

        async def delete(self, row):
            deleted.append(row)

        async def commit(self):
            return None

    async def run():
        data = await records_mod.clear_mine_records(
            FakeDb(),  # type: ignore[arg-type]
            user=SimpleNamespace(id=1, username="admin"),  # type: ignore[arg-type]
            admin=True,
        )
        assert data["deleted"] == 2
        assert data["skipped"] == 0
        assert data["ids"] == ["proc000000000001", "pend000000000001"]
        assert len(deleted) == 2
        assert not (out / "keepme00000001.docx").exists()
        assert not (out / "locked00000001.docx").exists()

        skipped = await records_mod.clear_mine_records(
            FakeDb(),  # type: ignore[arg-type]
            user=SimpleNamespace(id=1, username="op"),  # type: ignore[arg-type]
            admin=False,
        )
        assert skipped["deleted"] == 1
        assert skipped["skipped"] == 1
        assert skipped["ids"] == ["proc000000000001"]

    asyncio.run(run())

