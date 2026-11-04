import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from docx import Document
from sqlalchemy import select
from db.models.tender import TenderRecord
from db.session import AsyncSessionLocal, engine
from api.services.tenders.gen_biz_resp import draft_business_response
from api.services.tenders.generate import generate_bid
from api.services.tenders.native_format import validate_native_toc
from api.services.tenders.schema import BidBrief

engine.echo = False


class Unexpected:
    async def complete(self, *_args, **_kwargs):
        raise AssertionError('Original fixed declaration must not invoke LLM')


async def main():
    async with AsyncSessionLocal() as db:
        raw = (await db.execute(select(TenderRecord.brief_json).where(
            TenderRecord.docx_file == '482255dd3177.docx'
        ))).scalar_one()
    original = BidBrief.model_validate(raw)
    fixed = next(item for item in original.outlineItems if item.title == '承诺书')
    bad_parts = [part.strip() for part in fixed.draftBody.split('\n\n') if part.strip()]
    source = Path('storage/tenders/ffa587cb26d0.format.docx').resolve()
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    results = []
    for scenario in ('cached-snapshot', 'redrafted'):
        data = original.model_copy(deep=True)
        if scenario == 'redrafted':
            data, notes = await draft_business_response(None, data, client=Unexpected())
            assert any('原版固定承诺' in note for note in notes)
            assert next(i for i in data.outlineItems if i.title == '承诺书').draftBody == ''
        folder = Path('tmp/business-form-guard') / scenario
        folder.mkdir(exist_ok=True)
        with patch('api.services.tenders.generate.tenders_output_dir', return_value=folder):
            result = generate_bid(data, catalog_slots=[], catalog_media={})
        doc = Document(folder / result['docxFile'])
        texts = [p.text for p in doc.paragraphs]
        assert not any(part in texts for part in bad_parts)
        assert sum('现阶段没有担任任何在施建设工程项目的项目经理' in text for text in texts) == 1
        stats = validate_native_toc(doc)
        assert stats['entries'] == 21
        stats.update(scenario=scenario, removedDraftParagraphs=len(bad_parts),
                     originalDeclarationRetained=True, path=str((folder / result['docxFile']).resolve()))
        results.append(stats)
        print(json.dumps(stats), flush=True)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert next(i for i in original.outlineItems if i.title == '承诺书').draftBody == fixed.draftBody
    Path('tmp/business-form-guard/replay.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    await engine.dispose()


asyncio.run(main())
