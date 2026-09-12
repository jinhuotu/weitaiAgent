"""永中 Web Office 内网改稿适配。"""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse

import pytest

from api.services.tenders.yozo import (
    build_iframe_url,
    generate_sign,
    handle_callback,
)
from common.config import get_settings


@pytest.fixture
def yozo_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("TENDER_DOC_PREVIEW", "yozo")
    monkeypatch.setenv("YOZO_DOCUMENT_SERVER_URL", "http://127.0.0.1:8088")
    monkeypatch.setenv("YOZO_OPEN_PATH", "/")
    monkeypatch.setenv("YOZO_OPEN_STYLE", "query")
    monkeypatch.setenv("YOZO_PUBLIC_API_BASE", "http://host.docker.internal:8100")
    monkeypatch.setenv("ONLYOFFICE_JWT_SECRET", "test-onlyoffice-secret")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    return get_settings()


def _seed_docx(tmp_path, name: str = "deadbeef0001.docx") -> str:
    out_dir = tmp_path / "tenders"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / name
    path.write_bytes(b"PK\x03\x04before")
    return name


def test_generate_sign_is_stable() -> None:
    secret = "qwe1qa"
    first = generate_sign(secret, {"appId": ["123"], "fileVersionIds": ["234", "123"]})
    second = generate_sign(secret, {"fileVersionIds": ["123", "234"], "appId": ["123"]})
    assert first == second
    assert first == first.upper()
    assert len(first) == 64


def test_doc_preview_engine_yozo_when_set(yozo_settings) -> None:
    assert yozo_settings.doc_preview_engine == "yozo"


def test_doc_preview_engine_yozo_falls_back_without_url(monkeypatch) -> None:
    monkeypatch.setenv("TENDER_DOC_PREVIEW", "yozo")
    monkeypatch.setenv("YOZO_DOCUMENT_SERVER_URL", "")
    get_settings.cache_clear()
    assert get_settings().doc_preview_engine == "browser"


def test_build_iframe_url_private_remote(tmp_path, yozo_settings) -> None:
    name = _seed_docx(tmp_path)
    url = build_iframe_url(
        name,
        download_name="投标文件.docx",
        user_id="1",
        user_name="tester",
        mode="edit",
        settings=yozo_settings,
    )
    parsed = urlparse(url)
    assert parsed.scheme == "http"
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port == 8088
    query = parse_qs(parsed.query)
    assert query["fileId"] == [name]
    assert query["fileName"] == ["投标文件.docx"]
    assert query["userRight"] == ["1"]
    assert "yozo-download" in unquote(query["fileUrl"][0])
    assert query["callbackUrl"][0].endswith(f"/tenders/files/{name}/yozo-callback")


def test_build_iframe_url_view_mode(tmp_path, yozo_settings) -> None:
    name = _seed_docx(tmp_path)
    url = build_iframe_url(
        name,
        download_name="投标文件.docx",
        user_id="1",
        user_name="tester",
        mode="view",
        settings=yozo_settings,
    )
    query = parse_qs(urlparse(url).query)
    assert query["userRight"] == ["0"]


def test_build_iframe_url_json_params(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TENDER_DOC_PREVIEW", "yozo")
    monkeypatch.setenv("YOZO_DOCUMENT_SERVER_URL", "http://127.0.0.1:8088")
    monkeypatch.setenv("YOZO_OPEN_STYLE", "json")
    monkeypatch.setenv("YOZO_PUBLIC_API_BASE", "http://host.docker.internal:8100")
    monkeypatch.setenv("ONLYOFFICE_JWT_SECRET", "test-onlyoffice-secret")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    name = _seed_docx(tmp_path)
    url = build_iframe_url(
        name,
        download_name="投标文件.docx",
        user_id="1",
        user_name="tester",
        settings=get_settings(),
    )
    query = parse_qs(urlparse(url).query)
    assert "jsonParams" in query
    assert name in query["jsonParams"][0]


@pytest.mark.asyncio
async def test_handle_callback_saves_raw_docx(tmp_path, yozo_settings) -> None:
    del yozo_settings
    name = _seed_docx(tmp_path)
    path = tmp_path / "tenders" / name

    class FakeRequest:
        headers = {"content-type": "application/octet-stream"}

        async def body(self):
            return b"PK\x03\x04after-edit"

    result = await handle_callback(name, FakeRequest())
    assert result["error"] == 0
    assert result["errorCode"] == "0"
    assert path.read_bytes() == b"PK\x03\x04after-edit"


@pytest.mark.asyncio
async def test_handle_callback_saves_from_url(tmp_path, monkeypatch, yozo_settings) -> None:
    del yozo_settings
    name = _seed_docx(tmp_path)
    path = tmp_path / "tenders" / name

    class FakeRequest:
        headers = {"content-type": "application/json"}

        async def body(self):
            return b'{"errorCode":"0","url":"http://yozo/saved.docx"}'

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url: str):
            class Resp:
                content = b"PK\x03\x04from-url"

                @staticmethod
                def raise_for_status() -> None:
                    return None

            return Resp()

    from api.services.tenders import yozo as yozo_mod

    monkeypatch.setattr(yozo_mod.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    result = await handle_callback(name, FakeRequest())
    assert result["error"] == 0
    assert path.read_bytes() == b"PK\x03\x04from-url"
