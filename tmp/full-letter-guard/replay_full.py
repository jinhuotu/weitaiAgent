import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from docx import Document
from docx.oxml.ns import qn
from sqlalchemy import select
from db.models.tender import TenderRecord
from db.session import AsyncSessionLocal, engine
from api.services.tenders.full_bid_sections import letter_region
from api.services.tenders.generate import generate_bid
from api.services.tenders.native_format import validate_native_toc
from api.services.tenders.word_structure import numbering_info
from api.services.tenders.assemble import _fill_drafted_format_sections
from api.services.tenders.schema import BidBrief

engine.echo = False


async def main():
    async with AsyncSessionLocal() as db:
        raw = (await db.execute(select(TenderRecord.brief_json).where(
            TenderRecord.docx_file == '1192bcbc9bbb.docx'
        ))).scalar_one()
    brief = BidBrief.model_validate(raw)
    source = Path('storage/tenders/3d0a4ef456f5.format.docx')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    folder = Path('tmp/full-letter-guard/full')
    folder.mkdir(exist_ok=True)
    with patch('api.services.tenders.generate.tenders_output_dir', return_value=folder):
        result = generate_bid(brief, catalog_slots=[], catalog_media={})
    path = folder / result['docxFile']
    doc = Document(path)
    region = letter_region(doc, brief)
    letter_text = '\n'.join(p.text for p in doc.paragraphs if p._p in region)
    assert '【待补充】' not in letter_text
    assert '技术得分点对照表' not in letter_text
    start = next(n for n, p in enumerate(doc.paragraphs) if '投标文件包括下列内容' in p.text)
    end = next(n for n, p in enumerate(doc.paragraphs) if '投标文件的上述组成部分' in p.text)
    composition = [p.text for p in doc.paragraphs[start + 1:end] if p._p in region
                   and numbering_info(doc, p._p) is not None
                   and numbering_info(doc, p._p)[:2] == ('181', 2)]
    assert len(composition) == 9
    pending = '【待补充】「施工组织设计」技术方案正文未完整生成，请结合本项目招标要求补充。'
    assert pending not in [p.text for p in doc.paragraphs]
    assert any('未追加重复' in note for note in result['warnings'])
    stats = validate_native_toc(doc)
    assert stats['entries'] == 21
    before = doc.element.xml
    repeated, _ = _fill_drafted_format_sections(path, brief, full_bid=True)
    stats.update(letterCompositionItems=len(composition), letterHasPending=False,
                 sourceUnchanged=digest == hashlib.sha256(source.read_bytes()).hexdigest(),
                 repeatedInsertions=repeated, repeatStable=Document(path).element.xml == before,
                 path=str(path.resolve()))
    print(json.dumps(stats), flush=True)
    Path('tmp/full-letter-guard/replay.json').write_text(json.dumps(stats, indent=2), encoding='utf-8')
    await engine.dispose()


asyncio.run(main())
