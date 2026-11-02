import json
from unittest.mock import AsyncMock
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
    monkeypatch.setattr(module.asyncio,'sleep',AsyncMock())
    class Client:
        calls={}
        async def complete(self,messages,**kwargs):
            index=json.loads(messages[1]['content'].split('\n')[0])['index']
            self.calls[index]=self.calls.get(index,0)+1
            if index==1 and self.calls[index]<3:
                raise AppError(ErrorCode.INTERNAL,'connect timeout',503)
            return '{"notes":[]}'
    client=Client()
    for _ in range(2):
        await module.extract_chunks('x'*36000,client,'json',lambda x:x,json.loads)
    assert client.calls=={1:3,2:1}
