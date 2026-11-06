"""Independent scoring fixtures for caching and concurrent HTTP callers."""
import asyncio
import json

import pytest

from api.services import tender_comparison as review


QUOTE = '近三年同类业绩，每份合同2分，最高6分；须提供合同及验收证明。'


@pytest.fixture
def client(monkeypatch):
    review._SCORING_CACHE.clear()

    class Client:
        api_base = 'https://test.invalid/v1'
        fixed_model = 'test-model'
        fixed_temperature = 0
        api_key = 'test-key'
        timeout_seconds = 120

        def __init__(self):
            self.calls = 0
            self.options = []
            self.respond = self.valid_response

        def valid_response(self, data):
            rows = []
            reviews = []
            for unit in data['units']:
                scoring = unit['text'] == QUOTE
                if scoring:
                    rows.append({'sourceUnitId': unit['id'], 'requirement': QUOTE,
                                 'maxScore': 6, 'volume': 'business', 'scopeKind': 'common'})
                reviews.append({'id': unit['id'],
                                'status': 'extracted' if scoring else 'not_scoring',
                                'reason': '完整业绩评分条款' if scoring else '普通供货说明'})
            return {'rows': rows, 'unitReviews': reviews}

        async def complete(self, messages, **kwargs):
            self.calls += 1
            self.options.append(kwargs)
            result = self.respond(json.loads(messages[-1]['content']))
            if isinstance(result, Exception):
                raise result
            return json.dumps(result, ensure_ascii=False)

    instance = Client()

    async def build(*args):
        return instance

    monkeypatch.setattr(review, 'build_llm_client', build)
    yield instance
    review._SCORING_CACHE.clear()


def test_structured_output_cache_preserves_fields_and_owns_results(client, caplog):
    caplog.set_level('INFO', logger=review.__name__)
    first = asyncio.run(review.extract_scoring(None, QUOTE))
    first['rows'][0]['requirement'] = 'changed by caller'
    second = asyncio.run(review.extract_scoring(None, QUOTE))
    assert client.calls == 1
    assert client.options == [{'mode': 'fast', 'response_format': {'type': 'json_object'}}]
    assert second['complete'] is True
    assert second['reviewComplete'] is False
    row = second['rows'][0]
    assert row['requirement'] == QUOTE
    assert row['maxScore'] == 6
    assert row['volume'] == 'business'
    assert row['sourceOffset'] == 0
    assert row['conditionsComplete'] is True
    assert row['scopeKind'] == 'common'
    assert row['applicability'] == 'common'
    assert row['status'] == 'indeterminate'
    assert second['coverage']['pendingUnits'] == 0
    for field in ('queue_seconds=', 'model_seconds=', 'parse_seconds=',
                  'validation_seconds=', 'total_seconds=', 'cache=hit'):
        assert field in caplog.text


@pytest.mark.parametrize('change', ['scope', 'model', 'endpoint', 'credential', 'prompt', 'source', 'version'])
def test_cache_identity_separates_inputs(client, monkeypatch, change):
    asyncio.run(review.extract_scoring(None, QUOTE))
    scope = None
    source = QUOTE
    if change == 'scope':
        scope = {'lotName': '标段乙'}
    elif change == 'model':
        client.fixed_model = 'another-model'
    elif change == 'endpoint':
        client.api_base = 'https://another.invalid/v1'
    elif change == 'credential':
        client.api_key = 'another-key'
    elif change == 'prompt':
        monkeypatch.setattr(review, '_EXTRACT_PROMPT', review._EXTRACT_PROMPT + '补充提示')
    elif change == 'source':
        source += '\n按合同供货。'
    else:
        monkeypatch.setattr(review, 'VERSION', 'test-next-version')
    asyncio.run(review.extract_scoring(None, source, scope=scope))
    assert client.calls == 2


@pytest.mark.parametrize('failure', ['transport', 'schema', 'coverage', 'provenance'])
def test_failed_or_unvalidated_batches_are_retried(client, failure):
    if failure == 'transport':
        client.respond = lambda data: RuntimeError('offline')
    elif failure == 'schema':
        client.respond = lambda data: {'rows': {}}
    elif failure == 'coverage':
        client.respond = lambda data: {'rows': [], 'unitReviews': []}
    else:
        client.respond = lambda data: {
            'rows': [{'requirement': '不存在的评分原文', 'scopeKind': 'common'}],
            'unitReviews': [{'id': data['units'][0]['id'], 'status': 'not_scoring', 'reason': '错误声明'}],
        }
    first = asyncio.run(review.extract_scoring(None, QUOTE))
    assert first['complete'] is False
    assert not review._SCORING_CACHE
    client.respond = client.valid_response
    second = asyncio.run(review.extract_scoring(None, QUOTE))
    assert client.calls == 2
    assert second['complete'] is True


def test_good_batches_survive_other_batch_failure(client):
    source = QUOTE + '\n' + '\n'.join('普通供货说明' + str(i) for i in range(40))
    valid = client.valid_response
    client.respond = lambda data: valid(data) if QUOTE in data['source'] else RuntimeError('offline')
    first = asyncio.run(review.extract_scoring(None, source))
    assert first['complete'] is False
    assert first['coverage']['reviewedUnits'] == 40
    assert client.calls == 2
    client.respond = valid
    second = asyncio.run(review.extract_scoring(None, source))
    assert second['complete'] is True
    assert second['coverage']['reviewedUnits'] == 41
    assert client.calls == 3


def test_duplicate_callers_share_work_and_cancellation_is_isolated(client, monkeypatch):
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        original = client.complete

        async def blocked(messages, **kwargs):
            entered.set()
            await release.wait()
            return await original(messages, **kwargs)

        monkeypatch.setattr(client, 'complete', blocked)
        first = asyncio.create_task(review.extract_scoring(None, QUOTE))
        await entered.wait()
        second = asyncio.create_task(review.extract_scoring(None, QUOTE))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        result = await second
        assert result['complete'] is True
        assert client.calls == 1
        assert not asyncio.get_running_loop()._tender_scoring_batches[1]

    asyncio.run(scenario())


def test_cache_expiration_and_capacity(client, monkeypatch):
    clock = [0]
    monkeypatch.setattr(review, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(review, '_SCORING_CACHE_LIMIT', 1)
    asyncio.run(review.extract_scoring(None, QUOTE))
    clock[0] = review._SCORING_CACHE_TTL + 1
    asyncio.run(review.extract_scoring(None, QUOTE))
    assert client.calls == 2
    asyncio.run(review.extract_scoring(None, QUOTE, scope={'lotName': '乙'}))
    assert len(review._SCORING_CACHE) == 1
    asyncio.run(review.extract_scoring(None, QUOTE))
    assert client.calls == 4


def test_global_scoring_concurrency_remains_bounded(client, monkeypatch):
    async def scenario():
        active = peak = 0
        original = client.complete

        async def counted(messages, **kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0)
                return await original(messages, **kwargs)
            finally:
                active -= 1

        monkeypatch.setattr(client, 'complete', counted)
        results = await asyncio.gather(*(review.extract_scoring(
            None, QUOTE, scope={'lotName': str(i)},
        ) for i in range(8)))
        assert all(result['complete'] for result in results)
        assert peak == 3
        assert client.calls == 8

    asyncio.run(scenario())
