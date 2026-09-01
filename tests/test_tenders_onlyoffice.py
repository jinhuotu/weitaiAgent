"""OnlyOffice 在线 Word 集成。"""

from __future__ import annotations

from pathlib import Path

import pytest
from jose import jwt

from api.services.tenders.onlyoffice import (
    build_editor_config,
    create_download_token,
    document_key,
    verify_download_token,
)
from api.services.tenders.schema import BidBrief
from common.config import Settings, get_settings


@pytest.fixture
def oo_settings(monkeypatch, tmp_path) -> Settings:
    monkeypatch.setenv("ONLYOFFICE_DOCUMENT_SERVER_URL", "http://127.0.0.1:8082")
    monkeypatch.setenv("ONLYOFFICE_JWT_SECRET", "test-onlyoffice-secret")
    monkeypatch.setenv("ONLYOFFICE_PUBLIC_API_BASE", "http://host.docker.internal:8100")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    return get_settings()


def test_document_key_changes_when_file_changes(tmp_path, oo_settings) -> None:
    del oo_settings
    path = tmp_path / "a.docx"
    path.write_bytes(b"hello")
    k1 = document_key(path)
    path.write_bytes(b"hello-world")
    k2 = document_key(path)
    assert k1 != k2


def test_download_token_roundtrip(oo_settings) -> None:
    token = create_download_token("abc123.docx", settings=oo_settings)
    assert verify_download_token(token, "abc123.docx", settings=oo_settings)
    assert not verify_download_token(token, "other.docx", settings=oo_settings)


def test_build_editor_config_structure(tmp_path, oo_settings) -> None:
    from api.services.tenders.document import build_bid_docx

    brief = BidBrief(
        projectName="测试项目",
        tenderer="招标人",
        bidPriceYuan=1000,
        legalPersonName="张三",
        attachQualifications=False,
    )
    out_dir = tmp_path / "tenders"
    out_dir.mkdir()
    dest = out_dir / "deadbeef0001.docx"
    build_bid_docx(brief, dest, qualification_pdf=None)

    config = build_editor_config(
        "deadbeef0001.docx",
        download_name="测试-投标文件.docx",
        user_id="1",
        user_name="tester",
        editor_height_px=820,
        settings=oo_settings,
    )
    assert config["documentType"] == "word"
    assert config["document"]["fileType"] == "docx"
    assert config["document"]["title"] == "测试-投标文件.docx"
    assert config["height"] == "100%"
    assert config["width"] == "100%"
    assert "onlyoffice-download" in config["document"]["url"]
    assert "token=" in config["document"]["url"]
    assert config["editorConfig"]["callbackUrl"].endswith("/onlyoffice-callback")
    assert config["editorConfig"]["lang"] == "zh-CN"
    assert config["editorConfig"]["region"] == "zh-CN"
    customization = config["editorConfig"]["customization"]
    assert customization["spellcheck"] is False
    assert customization["features"]["spellcheck"] is False
    assert customization["zoom"] == -2
    assert customization["hideRightMenu"] is True
    assert customization["compatibleFeatures"] is True
    assert "token" in config

    decoded = jwt.decode(
        config["token"],
        oo_settings.onlyoffice_jwt_secret,
        algorithms=["HS256"],
    )
    assert decoded["document"]["key"] == config["document"]["key"]


@pytest.mark.asyncio
async def test_handle_callback_saves_file(tmp_path, monkeypatch, oo_settings) -> None:
    from api.services.tenders import onlyoffice as oo_mod

    out_dir = tmp_path / "tenders"
    out_dir.mkdir()
    file_name = "deadbeef0001.docx"
    path = out_dir / file_name
    path.write_bytes(b"before")

    async def fake_get(url: str):
        class Resp:
            content = b"after-edit"

            @staticmethod
            def raise_for_status() -> None:
                return None

        return Resp()

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url: str):
            return await fake_get(url)

    monkeypatch.setattr(oo_mod.httpx, "AsyncClient", lambda **kwargs: FakeClient())

    result = await oo_mod.handle_callback(file_name, {"status": 2, "url": "http://x/doc"})
    assert result == {"error": 0}
    assert path.read_bytes() == b"after-edit"
