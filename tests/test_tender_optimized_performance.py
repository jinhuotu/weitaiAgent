import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.tenders.extract_chunks_optimized import _CACHE, extract_chunks, split_invitation


def test_144k_coverage_and_18k_budget():
    source = '完整原文。\n' * 24000
    chunks = split_invitation(source)
    assert ''.join(c.text for c in chunks) == source
    assert all(len(c.text) <= 18000 for c in chunks)
    assert len(chunks) <= 9


@pytest.mark.asyncio
async def test_each_range_called_once_json_mode_and_concurrency():
    _CACHE.clear()
    class Client:
        calls = []
        active = 0
        peak = 0
        async def complete(self, messages, *, mode, response_format=None):
            assert response_format == {'type':'json_object'}
            self.calls.append(json.loads(messages[1]['content'].split('\n')[0]))
            self.active += 1
            self.peak = max(self.active, self.peak)
            await asyncio.sleep(0)
            self.active -= 1
            return '{"notes":[],}'
    client = Client()
    text = 'a' * 144000
    await extract_chunks(text, client, 'json', lambda s:s, json.loads, concurrency=6)
    assert len(client.calls) == 8
    assert len({(c['start'], c['end']) for c in client.calls}) == 8
    assert client.peak == 6


@pytest.mark.asyncio
async def test_bulk_material_creation_has_constant_catalog_reads(monkeypatch):
    from api.services.tenders import library_kb
    from api.services.tenders.schema import PlaceholderItem
    rows = AsyncMock(return_value=[])
    monkeypatch.setattr(library_kb, 'list_library_items', rows)
    monkeypatch.setattr(library_kb, 'ensure_library_base', AsyncMock(return_value=SimpleNamespace(id=1)))
    monkeypatch.setattr(library_kb, 'find_catalog_item', lambda *a, **k:None)
    class DB:
        added = []
        commits = 0
        def add_all(self, values): self.added.extend(values)
        async def commit(self): self.commits += 1
    db = DB()
    result = await library_kb.persist_parsed_materials(db, [PlaceholderItem(key=f'item{i}',title=f'材料{i}',attachToSection='原文父章') for i in range(100)])
    assert rows.await_count == 1
    assert db.commits == 1 and len(db.added) == 100
    assert all(item.attachToSection == '原文父章' for item in result)
