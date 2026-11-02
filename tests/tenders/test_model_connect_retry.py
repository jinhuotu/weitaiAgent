import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from api.services.ai import llm
from common.errors import AppError


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [httpx.ConnectTimeout, httpx.ConnectError])
async def test_connection_setup_recovers_without_restarting_generation(monkeypatch, failure):
    calls = []
    sleep = AsyncMock()
    monkeypatch.setattr(llm.asyncio, 'sleep', sleep)

    async def send(self, request):
        calls.append(request.content)
        if len(calls) == 1:
            raise failure('temporary setup failure', request=request)
        return httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n')

    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', send)
    client = llm.LLMClient(api_base='https://model.invalid/v1', api_key='fixture', model='test')
    assert await client.complete([{'role': 'user', 'content': 'test'}]) == 'ok'
    assert len(calls) == 2 and calls[0] == calls[1]
    sleep.assert_awaited_once_with(0.5)


@pytest.mark.asyncio
async def test_persistent_connect_timeout_is_bounded(monkeypatch):
    send = AsyncMock(side_effect=httpx.ConnectTimeout('unavailable'))
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', send)
    monkeypatch.setattr(llm.asyncio, 'sleep', AsyncMock())
    client = llm.LLMClient(api_base='https://model.invalid/v1', api_key='fixture', model='test')
    with pytest.raises(AppError) as caught:
        await client.complete([{'role': 'user', 'content': 'test'}])
    assert caught.value.status_code == 503
    assert send.await_count == 3


@pytest.mark.asyncio
async def test_read_timeout_after_partial_output_never_replays(monkeypatch):
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
            raise httpx.ReadTimeout('stream interrupted')

    send = AsyncMock(return_value=httpx.Response(200, stream=BrokenStream()))
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', send)
    client = llm.LLMClient(api_base='https://model.invalid/v1', api_key='fixture', model='test')
    received = []
    with pytest.raises(AppError) as caught:
        async for piece in client.stream_chat([{'role': 'user', 'content': 'test'}]):
            received.append(piece)
    assert received == ['first']
    assert caught.value.status_code == 504 and send.await_count == 1


@pytest.mark.asyncio
async def test_cancellation_is_not_retried(monkeypatch):
    send = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', send)
    async with llm._http_client() as client:
        with pytest.raises(asyncio.CancelledError):
            await client.post('https://model.invalid/v1/chat/completions', json={})
    assert send.await_count == 1
