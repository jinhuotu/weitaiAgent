import asyncio
import pytest
from api.services.tenders.generation_parallel import ordered_generation


@pytest.mark.asyncio
async def test_bounded_ordered_generation_matches_serial(monkeypatch):
    active = peak = 0
    async def job(index):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep((10-index)*0.001)
        active -= 1
        return {'chapter':index,'body':f'body {index}'}
    jobs = [lambda index=index:job(index) for index in range(10)]
    monkeypatch.setenv('TENDER_GENERATE_CONCURRENCY','1')
    serial = await ordered_generation(jobs,stage='test')
    monkeypatch.setenv('TENDER_GENERATE_CONCURRENCY','5')
    parallel = await ordered_generation(jobs,stage='test')
    assert parallel == serial
    assert peak == 5


@pytest.mark.asyncio
async def test_failure_never_returns_incomplete_results():
    async def failure(): raise ValueError('missing body')
    with pytest.raises(ValueError):
        await ordered_generation([failure],stage='test')


@pytest.mark.asyncio
async def test_technical_plan_parallel_batches_keep_all_titles_and_manual_body():
    import json
    from api.services.tenders.schema import BidBrief, OutlineItem
    from api.services.tenders.gen_tech_plan import draft_technical_plan
    brief = BidBrief(outlineItems=[OutlineItem(id=str(i),title=f'技术章节{i}',kind='tech_chapter') for i in range(10)])
    brief.outlineItems.append(OutlineItem(id='manual',title='人工章节',kind='tech_chapter',draftStatus='edited',draftBody='人工原文'))
    class Client:
        active = peak = calls = 0
        async def complete(self, messages, *, mode, response_format=None):
            self.calls += 1
            self.active += 1
            self.peak = max(self.peak,self.active)
            assert response_format == {'type':'json_object'}
            titles = messages[-1]['content'].split('【本轮仅允许填充的既有章节标题，按此顺序】\n')[-1].splitlines()
            await asyncio.sleep(0.001)
            self.active -= 1
            return json.dumps({'chapters':[{'title':t,'body':t+'完整技术正文'} for t in titles]})
    client = Client()
    result, _ = await draft_technical_plan(None, brief, client=client)
    assert client.calls == 5 and client.peak == 5
    assert [i.title for i in result.outlineItems] == [i.title for i in brief.outlineItems]
    assert all(i.draftBody for i in result.outlineItems)
    assert result.outlineItems[-1].draftBody == '人工原文'
    assert not brief.outlineItems[0].draftBody
