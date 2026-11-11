import asyncio
import json

import httpx
import pytest
from common.errors import AppError,ErrorCode


@pytest.mark.asyncio
@pytest.mark.parametrize('error,status,label',[(httpx.ConnectTimeout,503,'20秒'),(httpx.ReadTimeout,504,'120s')])
async def test_connection_timeout_is_distinct_from_model_read(monkeypatch,error,status,label):
    from api.services.ai import llm
    def fail(request): raise error('fixture timeout',request=request)
    monkeypatch.setattr(llm,'_http_client',lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(fail)))
    client=llm.LLMClient(api_base='https://model.invalid/v1',api_key='fixture',model='test')
    with pytest.raises(AppError) as caught:
        await client.complete([{'role':'user','content':'test'}])
    assert caught.value.status_code==status
    assert label in caught.value.msg


@pytest.mark.asyncio
async def test_connect_failure_retries_only_failed_block_and_reuses_success(monkeypatch):
    from api.services.tenders import extract_chunks_optimized as module
    module._CACHE.clear()
    monkeypatch.setattr(module, "_RETRY_BACKOFF_SECONDS", 0)
    class Client:
        calls={}
        async def complete(self,messages,**kwargs):
            index=json.loads(messages[1]['content'].split('\n')[0])['index']
            self.calls[index]=self.calls.get(index,0)+1
            if index==1 and self.calls[index]==1:
                raise AppError(ErrorCode.INTERNAL,'connect timeout',503)
            return '{"notes":[]}'
    client=Client()
    for _ in range(2):
        await module.extract_chunks('x'*36000,client,'json',lambda x:x,json.loads)
    assert client.calls=={1:2,2:1}


@pytest.mark.asyncio
@pytest.mark.parametrize("persistent_timeout", [False, True])
async def test_slow_third_block_retries_once_without_returning_partial_results(monkeypatch, persistent_timeout):
    from api.services.tenders import extract_chunks_optimized as module

    monkeypatch.setattr(module, "_CHUNK_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(module, "_RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(module, "_CACHE", module._CACHE.__class__())
    calls = {}
    cancelled = []

    class Client:
        async def complete(self, messages, **kwargs):
            index = json.loads(messages[1]["content"].split("\n", 1)[0])["index"]
            calls[index] = calls.get(index, 0) + 1
            if index == 3 and (calls[index] == 1 or (persistent_timeout and calls[index] == 2)):
                try:
                    await asyncio.Future()
                finally:
                    cancelled.append(index)
            return json.dumps({"notes": [index], "materialReviewComplete": True})

    args = ("x" * 151331, Client(), "json", str, json.loads)
    if persistent_timeout:
        with pytest.raises(AppError) as caught:
            await module.extract_chunks(*args)
        assert caught.value.status_code == 504
        assert "第 3 / 9 块" in caught.value.msg
        assert calls[3] == 2

    payload = await module.extract_chunks(*args)
    assert payload == {"notes": list(range(1, 10)), "materialReviewComplete": True}
    assert calls == {index: (2 + int(persistent_timeout) if index == 3 else 1) for index in range(1, 10)}
    assert cancelled == [3] * (1 + int(persistent_timeout))


@pytest.mark.asyncio
async def test_extraction_deadline_includes_chunks_waiting_for_a_slot(monkeypatch, caplog):
    from api.services.tenders import extract_chunks_optimized as module

    monkeypatch.setattr(module, "_EXTRACTION_TIMEOUT_SECONDS", 0.03)
    calls = []
    cancelled = []

    class Client:
        async def complete(self, messages, **kwargs):
            index = json.loads(messages[1]["content"].split("\n", 1)[0])["index"]
            calls.append(index)
            try:
                await asyncio.Future()
            finally:
                cancelled.append(index)

    with pytest.raises(AppError) as caught:
        await module.extract_chunks("y" * 36000, Client(), "json", str, json.loads, concurrency=1)
    assert caught.value.status_code == 504
    assert "总时间上限" in caught.value.msg
    assert calls == cancelled == [1]
    assert "unfinished_chunks=[1, 2]" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_model_http_transient_status_is_retried_through_shared_client(monkeypatch, status):
    from api.services.ai import llm
    from api.services.tenders import extract_chunks_optimized as module

    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        return httpx.Response(status, text="fixture", request=request)

    monkeypatch.setattr(llm, "_http_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(module, "_RETRY_BACKOFF_SECONDS", 0)
    client = llm.LLMClient(api_base="https://model.invalid/v1", api_key="fixture", model="test")
    with pytest.raises(AppError, match=fr"LLM request failed \({status}\)"):
        await module.extract_chunks("fixture", client, "json", str, json.loads)
    assert calls == 2
