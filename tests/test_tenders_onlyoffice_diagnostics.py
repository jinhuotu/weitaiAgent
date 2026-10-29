"""Transport, protocol and authentication failures stay actionable through the API."""

import logging
from types import SimpleNamespace

import httpx
import pytest
from jose import jwt

from api.services.tenders import onlyoffice as oo
from common.config import Settings
from common.errors import AppError


@pytest.fixture
def oo_env(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        onlyoffice_document_server_url="http://127.0.0.1:8082",
        onlyoffice_jwt_secret="diagnostic-secret",
        jwt_secret_key="private-app-secret",
    )
    path = tmp_path / "deadbeef0001.docx"
    path.write_bytes(b"original document")
    monkeypatch.setattr(oo, "resolve_output_file", lambda _: path)
    return settings, path


@pytest.fixture
def mock_server(monkeypatch):
    sync, asynchronous = httpx.Client, httpx.AsyncClient

    def install(handler):
        transport = httpx.MockTransport(handler)
        options = []

        def make_sync(**kw):
            options.append(kw)
            return sync(transport=transport, **kw)

        def make_async(**kw):
            options.append(kw)
            return asynchronous(transport=transport, **kw)

        monkeypatch.setattr(oo.httpx, "Client", make_sync)
        monkeypatch.setattr(oo.httpx, "AsyncClient", make_async)
        return options

    return install


@pytest.mark.parametrize(
    "exception,message,status",
    [
        (httpx.ConnectError, "连不上 OnlyOffice", 503),
        (httpx.ConnectTimeout, "响应超时", 504),
        (httpx.ReadTimeout, "响应超时", 504),
        (httpx.RemoteProtocolError, "通信异常", 502),
    ],
)
@pytest.mark.asyncio
async def test_save_transport_failure_is_actionable(
    oo_env,
    mock_server,
    exception,
    message,
    status,
):
    settings, path = oo_env

    def fail(request):
        raise exception("failure", request=request)

    mock_server(fail)
    with pytest.raises(AppError, match=message) as caught:
        await oo.force_save_document(path.name, oo.document_key(path), settings=settings)
    assert caught.value.status_code == status
    assert path.read_bytes() == b"original document"
    if exception == httpx.ConnectError:
        assert "docker compose up -d onlyoffice" in caught.value.msg
        assert "8082" in caught.value.msg and "ONLYOFFICE_DOCUMENT_SERVER_URL" in caught.value.msg


@pytest.mark.parametrize(
    "status,body,message",
    [
        (502, "<html>nginx upstream failed</html>", "HTTP 502"),
        (302, "redirected elsewhere", "HTTP 302"),
        (403, "access denied", "JWT_SECRET"),
        (200, "<html>this is a login page</html>", "非 JSON"),
        (200, "[]", "响应格式异常"),
        (200, '{"error": false}', "响应格式异常"),
    ],
)
@pytest.mark.asyncio
async def test_save_logs_server_response_and_preserves_specific_error(
    oo_env,
    mock_server,
    caplog,
    status,
    body,
    message,
):
    settings, path = oo_env
    mock_server(lambda _: httpx.Response(status, text=body))
    with caplog.at_level(logging.WARNING, logger=oo.logger.name):
        with pytest.raises(AppError, match=message):
            await oo.force_save_document(path.name, oo.document_key(path), settings=settings)
    assert body in caplog.text
    assert path.read_bytes() == b"original document"


@pytest.mark.parametrize(
    "code,message,status",
    [
        (1, "会话不存在或已过期", 409),
        (2, "ONLYOFFICE_PUBLIC_API_BASE", 502),
        (3, "服务内部错误", 502),
        (4, "尚未收到修改", 409),
        (5, "不支持当前命令", 502),
        (6, "JWT 校验失败", 502),
        (99, "错误码 99", 502),
    ],
)
@pytest.mark.asyncio
async def test_command_failures_are_distinct(oo_env, mock_server, code, message, status):
    settings, path = oo_env
    mock_server(lambda _: httpx.Response(200, json={"error": code}))
    with pytest.raises(AppError, match=message) as caught:
        await oo.force_save_document(path.name, oo.document_key(path), settings=settings)
    assert caught.value.status_code == status


def test_preflight_checks_health_and_signed_read_only_version(oo_env, mock_server):
    import json

    settings, _ = oo_env
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path == "/healthcheck":
            return httpx.Response(200, text="true\n")
        payload = json.loads(request.content)
        assert jwt.decode(
            payload["token"], settings.onlyoffice_jwt_secret, algorithms=["HS256"]
        ) == {
            "c": "version",
        }
        assert payload["c"] == "version" and "key" not in payload
        return httpx.Response(200, json={"error": 0, "version": "8.2.2"})

    mock_server(handle)
    result = oo.check_document_server(settings=settings)
    assert result == {
        "healthy": True,
        "reachable": True,
        "commandAccepted": True,
        "jwtConfigured": True,
        "version": "8.2.2",
    }
    assert [r.method for r in requests] == ["GET", "POST"]
    assert settings.onlyoffice_jwt_secret not in str(result)


@pytest.mark.parametrize(
    "health,message", [("false", "健康检查未通过"), ("<html/>", "健康检查未通过")]
)
def test_unhealthy_server_stops_before_command(oo_env, mock_server, health, message):
    settings, _ = oo_env
    calls = []

    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(200, text=health)

    mock_server(handle)
    with pytest.raises(AppError, match=message):
        oo.check_document_server(settings=settings)
    assert calls == ["/healthcheck"]


def test_health_true_does_not_hide_jwt_mismatch(oo_env, mock_server):
    settings, _ = oo_env
    mock_server(
        lambda request: httpx.Response(
            200,
            text="true",
        )
        if request.url.path == "/healthcheck"
        else httpx.Response(200, json={"error": 6})
    )
    with pytest.raises(AppError, match="JWT 校验失败") as caught:
        oo.build_editor_config(
            "deadbeef0001.docx",
            download_name="",
            user_id="1",
            user_name="tester",
            settings=settings,
        )
    assert "ONLYOFFICE_JWT_SECRET" in caught.value.msg and "JWT_SECRET" in caught.value.msg


def test_editor_open_fails_before_returning_editable_config(oo_env, mock_server):
    settings, _ = oo_env

    def fail(request):
        raise httpx.ConnectError("refused", request=request)

    mock_server(fail)
    with pytest.raises(AppError, match="连不上 OnlyOffice"):
        oo.build_editor_config(
            "deadbeef0001.docx",
            download_name="",
            user_id="1",
            user_name="tester",
            settings=settings,
        )


def test_response_logs_redact_secrets_and_tokens(oo_env, caplog):
    settings, _ = oo_env
    token = jwt.encode({"c": "version"}, settings.onlyoffice_jwt_secret, algorithm="HS256")
    body = f"{settings.onlyoffice_jwt_secret} {settings.jwt_secret_key} {token}"
    with caplog.at_level(logging.WARNING, logger=oo.logger.name):
        oo._log_server_response(httpx.Response(502, text=body), settings, "test")
    assert settings.onlyoffice_jwt_secret not in caplog.text
    assert settings.jwt_secret_key not in caplog.text
    assert token not in caplog.text
    assert "[REDACTED]" in caplog.text


@pytest.mark.asyncio
async def test_diagnostics_route_keeps_specific_failure(monkeypatch):
    from api.routers import tenders

    def fail():
        raise AppError(msg="OnlyOffice JWT 校验失败", status_code=502)

    monkeypatch.setattr(tenders, "check_document_server", fail)
    with pytest.raises(AppError, match="JWT 校验失败"):
        await tenders.tenders_onlyoffice_health(SimpleNamespace(id=1))


@pytest.mark.parametrize("endpoint", ["health", "editor-config", "forcesave"])
@pytest.mark.asyncio
async def test_http_envelope_preserves_actionable_message(oo_env, monkeypatch, endpoint):
    from fastapi import FastAPI

    from api import deps
    from api.app import create_app
    from api.routers import tenders

    settings, path = oo_env
    settings.tender_doc_preview = "onlyoffice"
    app = FastAPI()
    # Exercise the production route and production exception handler, isolating
    # database/auth dependencies so this regression never needs a real account.
    app.add_exception_handler(AppError, create_app().exception_handlers[AppError])
    app.include_router(tenders.router, prefix="/api/v1")

    async def user():
        return SimpleNamespace(id=1, username="tester", display_name="tester")

    async def db():
        yield None

    app.dependency_overrides[deps.get_current_user] = user
    app.dependency_overrides[deps.get_db] = db
    monkeypatch.setattr(tenders, "get_settings", lambda: settings)
    monkeypatch.setattr(tenders, "onlyoffice_enabled", lambda: True)
    monkeypatch.setattr(tenders, "resolve_output_file", lambda _: path)
    message = "OnlyOffice JWT 校验失败：请核对 ONLYOFFICE_JWT_SECRET 与 JWT_SECRET"

    def fail(*args, **kwargs):
        raise AppError(msg=message, status_code=502)

    async def async_fail(*args, **kwargs):
        fail()

    monkeypatch.setattr(tenders, "check_document_server", fail)
    monkeypatch.setattr(tenders, "build_editor_config", fail)
    monkeypatch.setattr(tenders, "force_save_document", async_fail)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        if endpoint == "health":
            response = await client.get("/api/v1/tenders/onlyoffice/health")
        elif endpoint == "editor-config":
            response = await client.get("/api/v1/tenders/files/deadbeef0001.docx/editor-config")
        else:
            response = await client.post(
                "/api/v1/tenders/files/deadbeef0001.docx/onlyoffice-forcesave",
                json={"key": "current"},
            )
    assert response.status_code == 502
    assert response.json()["msg"] == message


@pytest.mark.asyncio
async def test_document_service_and_callback_download_do_not_inherit_system_proxy(
    oo_env,
    mock_server,
    monkeypatch,
):
    settings, path = oo_env
    monkeypatch.setattr(oo, "get_settings", lambda: settings)

    def handle(request):
        if request.url.path == "/healthcheck":
            return httpx.Response(200, text="true")
        if request.url.path == "/saved.docx":
            return httpx.Response(200, content=b"saved content")
        return httpx.Response(200, json={"error": 0, "version": "8.2.2"})

    options = mock_server(handle)
    oo.check_document_server(settings=settings)
    await oo.force_save_document(path.name, oo.document_key(path), settings=settings)
    assert await oo.handle_callback(
        path.name, {"status": 6, "url": "http://onlyoffice/saved.docx"}
    ) == {
        "error": 0,
    }
    assert path.read_bytes() == b"saved content"
    assert len(options) == 3 and all(kw.get("trust_env") is False for kw in options)
