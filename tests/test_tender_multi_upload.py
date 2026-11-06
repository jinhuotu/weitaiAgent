"""Independent upload boundary and merge examples (not seed-derived)."""
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import UploadFile
from common.errors import AppError
from api.services.tenders import extract, quote
from api.services.knowledge import parsers


def sheet(name, amount, headers=('名称', '数量', '金额')):
    return quote.QuoteSheet('报价', (quote.QuoteLine('99', name, '', '米', Decimal(2), Decimal(3), Decimal(amount)),), Decimal(0), Decimal(amount), Decimal(amount), '', Decimal(amount), headers=headers)


def test_quote_merge_skips_failure_and_header_mismatch(monkeypatch):
    examples = {'a': sheet('电缆', 6), 'b': None, 'c': sheet('接头', 8, ('不同表头',)), 'd': sheet('附件', 10)}
    monkeypatch.setattr(quote, 'parse_quote_path', lambda path: examples[path])
    merged, notes, names = quote.merge_quote_files([(x, x+'.xlsx') for x in examples])
    assert names == ['a.xlsx', 'd.xlsx']
    assert [(x.seq, x.name, x.qty, x.amount) for x in merged.lines] == [('1', '电缆', Decimal(2), Decimal(6)), ('2', '附件', Decimal(2), Decimal(10))]
    assert merged.headers == ('名称', '数量', '金额')
    assert merged.total_inc_tax == merged.total_ex_tax == merged.source_inc_tax == Decimal(16)
    assert len(notes) == 2 and 'b.xlsx' in notes[0] and 'c.xlsx' in notes[1]


def test_all_quote_failures(monkeypatch):
    monkeypatch.setattr(quote, 'parse_quote_path', lambda p: None)
    merged, notes, names = quote.merge_quote_files([('bad', '坏清单.xlsx')])
    assert merged is None and names == [] and '坏清单.xlsx' in notes[0]


@pytest.mark.asyncio
async def test_multi_invitation_budget_and_exact_join(monkeypatch):
    uploads = [UploadFile(filename=n+'.docx', file=BytesIO(b'x')) for n in ('主文件', '附件', '补充')]
    monkeypatch.setattr(extract, '_save_invitation', AsyncMock(side_effect=[Path('a.docx'), Path('b.docx'), Path('c.docx')]))
    parser = AsyncMock(side_effect=[SimpleNamespace(text='A'*25, ocr_pages=2), SimpleNamespace(text='B'*25), SimpleNamespace(text='C'*25, ocr_capped=True)])
    monkeypatch.setattr(extract, 'extract_text_from_file', parser)
    monkeypatch.setattr(extract, 'get_settings', lambda: SimpleNamespace(ocr_max_pages=2))
    saved = []
    monkeypatch.setattr(extract, 'save_invitation_text', lambda text, **kw: saved.append(text) or 'one-id')
    class Stop(Exception): pass
    async def stop(*a, **kw): raise Stop()
    monkeypatch.setattr(extract, '_recall_knowledge', stop)
    with pytest.raises(Stop): await extract.parse_invitation(None, uploads)
    assert saved == ['A'*25+'\n\n'+'B'*25+'\n\n'+'C'*25]
    assert [call.kwargs['ocr_budget'] for call in parser.call_args_list] == [2, 0, 0]


@pytest.mark.asyncio
async def test_invitation_failure_names_file(monkeypatch):
    monkeypatch.setattr(extract, '_save_invitation', AsyncMock(side_effect=ValueError('conversion failed')))
    with pytest.raises(AppError, match='坏文件.doc'):
        await extract.parse_invitation(None, UploadFile(filename='坏文件.doc', file=BytesIO(b'x')))


@pytest.mark.asyncio
async def test_default_ocr_call_unchanged_and_explicit_budget_forwarded(tmp_path, monkeypatch):
    path = tmp_path/'sample.docx'; path.write_bytes(b'x')
    reader = AsyncMock(side_effect=lambda *a, **kw: parsers.ExtractResult(text='valid document'))
    monkeypatch.setattr(parsers, '_read_docx', reader)
    await parsers.extract_text_from_file(path)
    assert reader.call_args.kwargs == {}
    await parsers.extract_text_from_file(path, ocr_budget=0)
    assert reader.call_args.kwargs == {'ocr_budget': 0}
@pytest.mark.asyncio
async def test_route_accepts_legacy_and_array_together(monkeypatch):
    from api.routers import tenders
    from api.services.tenders import demo
    monkeypatch.setattr(demo, 'demo_enabled', lambda: False)
    parse = AsyncMock(return_value={'brief': {}})
    monkeypatch.setattr(tenders, 'parse_invitation', parse)
    monkeypatch.setattr(tenders.kb_access, 'has_base_perm', AsyncMock(return_value=False))
    monkeypatch.setattr(tenders, 'list_slots_status_kb', AsyncMock(return_value=[]))
    a = UploadFile(filename='主.docx', file=BytesIO(b'a'))
    b = UploadFile(filename='附件.docx', file=BytesIO(b'b'))
    await tenders.tenders_parse_invitation(None, SimpleNamespace(id=1), file=b, files=[a], quoteFiles=None, quoteFile=None, quoteRecordIds=None, kbIds=None, current=None)
    assert parse.await_count == 1
    assert parse.call_args.args[1] == [a, b]
    with pytest.raises(AppError):
        await tenders.tenders_parse_invitation(None, SimpleNamespace(id=1), file=None, files=None, quoteFiles=None, quoteFile=None, quoteRecordIds=None, kbIds=None, current=None)
