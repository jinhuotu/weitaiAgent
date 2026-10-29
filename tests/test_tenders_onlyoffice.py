"""OnlyOffice 在线 Word 集成。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from jose import jwt

from api.services.tenders.onlyoffice import (
    build_editor_config,
    create_download_token,
    document_key,
    force_save_document,
    verify_download_token,
)
from api.services.tenders.schema import BidBrief
from common.config import Settings, get_settings


@pytest.fixture
def oo_settings(monkeypatch, tmp_path) -> Iterator[Settings]:
    monkeypatch.setenv("ONLYOFFICE_DOCUMENT_SERVER_URL", "http://127.0.0.1:8082")
    monkeypatch.setenv("ONLYOFFICE_JWT_SECRET", "test-onlyoffice-secret")
    monkeypatch.setenv("ONLYOFFICE_PUBLIC_API_BASE", "http://host.docker.internal:8100")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    try:
        yield get_settings()
    finally:
        get_settings.cache_clear()


def test_document_key_changes_when_file_changes(tmp_path, oo_settings) -> None:
    del oo_settings
    path = tmp_path / "a.docx"
    path.write_bytes(b"hello")
    k1 = document_key(path)
    path.write_bytes(b"hello-world")
    k2 = document_key(path)
    assert k1 != k2


def test_doc_preview_engine_defaults_to_browser(monkeypatch) -> None:
    # 空值走代码默认。本机 .env 可能已写成 onlyoffice，不能让它决定断言。
    monkeypatch.setenv("TENDER_DOC_PREVIEW", "")
    monkeypatch.setenv("ONLYOFFICE_DOCUMENT_SERVER_URL", "http://127.0.0.1:8082")
    get_settings.cache_clear()
    assert get_settings().doc_preview_engine == "browser"


def test_doc_preview_engine_onlyoffice_when_set(monkeypatch) -> None:
    monkeypatch.setenv("TENDER_DOC_PREVIEW", "onlyoffice")
    monkeypatch.setenv("ONLYOFFICE_DOCUMENT_SERVER_URL", "http://127.0.0.1:8082")
    get_settings.cache_clear()
    assert get_settings().doc_preview_engine == "onlyoffice"


def test_document_server_url_uses_lan_and_env_overrides(monkeypatch) -> None:
    from common import config

    monkeypatch.delenv("ONLYOFFICE_DOCUMENT_SERVER_URL", raising=False)
    monkeypatch.setattr(config, "_lan_ipv4", lambda: "192.168.31.202")
    assert Settings(_env_file=None).onlyoffice_document_server_url == "http://192.168.31.202:8082"
    monkeypatch.setenv("ONLYOFFICE_DOCUMENT_SERVER_URL", "http://192.168.2.119:8082")
    assert Settings(_env_file=None).onlyoffice_document_server_url == "http://192.168.2.119:8082"


def test_document_server_url_tracks_ip_change_and_falls_back(monkeypatch) -> None:
    from common import config

    monkeypatch.delenv("ONLYOFFICE_DOCUMENT_SERVER_URL", raising=False)
    monkeypatch.setattr(config, "_lan_ipv4", lambda: "192.168.31.202")
    assert Settings(_env_file=None).onlyoffice_document_server_url == "http://192.168.31.202:8082"
    monkeypatch.setattr(config, "_lan_ipv4", lambda: "192.168.31.203")
    assert Settings(_env_file=None).onlyoffice_document_server_url == "http://192.168.31.203:8082"
    monkeypatch.setattr(config, "_lan_ipv4", lambda: None)
    assert Settings(_env_file=None).onlyoffice_document_server_url == "http://127.0.0.1:8082"


def test_lan_ip_detector_falls_back_to_non_loopback_interface(monkeypatch) -> None:
    from common import config

    class NoRouteSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def connect(self, _target):
            raise OSError("no default route")

    monkeypatch.setattr(config.socket, "socket", lambda *_args: NoRouteSocket())
    monkeypatch.setattr(config.socket, "gethostname", lambda: "server")
    monkeypatch.setattr(
        config.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (None, None, None, None, ("127.0.0.1", 0)),
            (None, None, None, None, ("169.254.10.2", 0)),
            (None, None, None, None, ("192.168.31.202", 0)),
        ],
    )
    assert config._lan_ipv4() == "192.168.31.202"


def test_download_token_roundtrip(oo_settings) -> None:
    token = create_download_token("abc123.docx", settings=oo_settings)
    assert verify_download_token(token, "abc123.docx", settings=oo_settings)
    assert not verify_download_token(token, "other.docx", settings=oo_settings)


def test_build_editor_config_structure(tmp_path, monkeypatch, oo_settings) -> None:
    from api.services.tenders.document import build_bid_docx
    monkeypatch.setattr('api.services.tenders.onlyoffice.check_document_server', lambda **_: {})

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
    assert customization["forcesave"] is True
    assert "forceSave" not in customization
    assert "plugins" not in customization
    assert "-g4" in config["document"]["key"]
    assert "token" in config

    decoded = jwt.decode(
        config["token"],
        oo_settings.onlyoffice_jwt_secret,
        algorithms=["HS256"],
    )
    assert decoded["document"]["key"] == config["document"]["key"]


@pytest.mark.asyncio
async def test_force_save_document_uses_signed_command_and_current_key(
    tmp_path, monkeypatch, oo_settings
) -> None:
    from api.services.tenders import onlyoffice as oo_mod

    out_dir = tmp_path / "tenders"
    out_dir.mkdir()
    file_name = "deadbeef0001.format.docx"
    path = out_dir / file_name
    path.write_bytes(b"current-format")
    key = document_key(path)
    calls = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, params, json):
            calls.append((url, params, json))
            import httpx

            return httpx.Response(200, json={"error": 0, "key": key})

    monkeypatch.setattr(oo_mod.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    await force_save_document(file_name, key, settings=oo_settings)
    assert calls[0][0] == "http://127.0.0.1:8082/command"
    assert calls[0][1] == {"shardkey": key}
    assert jwt.decode(
        calls[0][2]["token"], oo_settings.onlyoffice_jwt_secret, algorithms=["HS256"]
    ) == {
        "c": "forcesave",
        "key": key,
    }

    path.write_bytes(b"newer-version")
    with pytest.raises(Exception, match="模板版本已变化"):
        await force_save_document(file_name, key, settings=oo_settings)
    assert len(calls) == 1


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
    assert not (tmp_path / "tender-invitations" / file_name).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [2, 6])
async def test_handle_callback_copies_format_template(
    tmp_path, monkeypatch, oo_settings, status
) -> None:
    from api.services.tenders import onlyoffice as oo_mod

    out_dir = tmp_path / "tenders"
    inv_dir = tmp_path / "tender-invitations"
    out_dir.mkdir()
    inv_dir.mkdir()
    file_name = "deadbeef0001.format.docx"
    path = out_dir / file_name
    kept = inv_dir / file_name
    path.write_bytes(b"before")
    kept.write_bytes(b"orig")

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url: str):
            del url

            class Resp:
                content = b"edited-format"

                @staticmethod
                def raise_for_status() -> None:
                    return None

            return Resp()

    monkeypatch.setattr(oo_mod.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    result = await oo_mod.handle_callback(file_name, {"status": status, "url": "http://x/doc"})
    assert result == {"error": 0}
    assert path.read_bytes() == b"edited-format"
    assert kept.read_bytes() == b"edited-format"


def test_confirm_format_template_commits_saved_preview(tmp_path, oo_settings) -> None:
    from api.services.tenders.onlyoffice import confirm_format_template
    from api.services.tenders.pdf_format import _ready_format_docx
    from api.services.tenders.slice_rules import SLICE_MARK

    file_name = "deadbeef0001.format.docx"
    out_dir = tmp_path / "tenders"
    inv_dir = tmp_path / "tender-invitations"
    out_dir.mkdir()
    inv_dir.mkdir()
    preview = out_dir / file_name
    template = inv_dir / file_name
    preview.write_bytes(b"edited-format" * 100)
    template.write_bytes(b"original-format" * 100)
    (inv_dir / f"{file_name}.source").write_text(SLICE_MARK, encoding="utf-8")

    assert confirm_format_template("deadbeef0001") == document_key(preview)
    assert template.read_bytes() == preview.read_bytes()
    assert _ready_format_docx("deadbeef0001") == template
