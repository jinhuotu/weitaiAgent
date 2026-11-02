from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import UploadFile
from io import BytesIO


@pytest.mark.asyncio
async def test_enabled_parse_branch_calls_real_chunk_interface(tmp_path, monkeypatch):
    from api.services.tenders import extract, extract_chunks_optimized as extract_chunks, library_kb
    path = tmp_path / 'invite.docx'
    path.write_bytes(b'fixture')
    monkeypatch.setattr(extract, '_save_invitation', AsyncMock(return_value=path))
    monkeypatch.setattr(extract, 'extract_text_from_file', AsyncMock(return_value=SimpleNamespace(text='A complete invitation document with enough text.')))
    monkeypatch.setattr(extract, 'save_invitation_text', lambda *a, **k: 'fixture')
    monkeypatch.setattr(extract, '_recall_knowledge', AsyncMock(return_value=([], [])))
    monkeypatch.setattr(extract, 'load_history_playbooks', AsyncMock(return_value=[]))
    monkeypatch.setattr(library_kb, 'catalog_placeholders', AsyncMock(return_value=[]))
    monkeypatch.setattr(library_kb, 'list_library_items', AsyncMock(return_value=[]))
    monkeypatch.setattr(extract, 'build_llm_client', AsyncMock(return_value=object()))
    class ReachedChunkStage(Exception):
        pass
    # Deliberately use the real function signature: unsupported arguments fail.
    async def chunk_stage(text, client, system, make_prompt, parse, retry_prompt='', **options):
        assert 'A complete invitation' in make_prompt(text)
        raise ReachedChunkStage()
    monkeypatch.setattr(extract_chunks, 'extract_chunks', chunk_stage)
    with pytest.raises(ReachedChunkStage):
        await extract.parse_invitation(object(), UploadFile(filename='invite.docx', file=BytesIO(b'fixture')))
