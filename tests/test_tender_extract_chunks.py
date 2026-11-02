import asyncio
import json

import pytest

from api.services.tenders.extract_chunks import _CACHE, extract_chunks, merge_payloads, split_invitation


@pytest.mark.parametrize("size", [1, 9999, 10000, 10001, 180000])
def test_lossless(size):
    text = ("Chapter\nParagraph complete.\n" * (size // 28 + 2))[:size]
    chunks = split_invitation(text)
    assert "".join(c.text for c in chunks) == text
    assert all(len(c.text) <= 18000 for c in chunks)
    assert chunks[-1].end == len(text)
    if size <= 18000:
        assert len(chunks) == 1


def test_reduce_keeps_arrays_and_fills_fields():
    assert merge_payloads([{"notes": ["a"], "name": "first"}, {"notes": ["a", "b"], "name": "second", "tail": 1}]) == {"notes": ["a", "b"], "name": "first", "tail": 1}


@pytest.mark.asyncio
async def test_invalid_json_does_not_retry_or_subdivide():
    _CACHE.clear()
    class Client:
        calls = 0
        async def complete(self, messages, mode):
            self.calls += 1
            return 'invalid'
    client = Client()
    with pytest.raises(json.JSONDecodeError):
        await extract_chunks('x' * 16000, client, 'json', lambda s:s, json.loads)
    assert client.calls == 1


@pytest.mark.asyncio
async def test_cache_skips_unchanged_ranges():
    _CACHE.clear()
    class Client:
        calls = 0
        async def complete(self, messages, mode):
            self.calls += 1
            return '{}'
    client = Client()
    for _ in range(2):
        await extract_chunks('source', client, 'json', lambda s:s, json.loads)
    assert client.calls == 1
