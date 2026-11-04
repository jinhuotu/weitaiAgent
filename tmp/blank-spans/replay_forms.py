import asyncio
import hashlib
import json
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from sqlalchemy import select
from db.models.tender import TenderRecord
from db.session import AsyncSessionLocal, engine
from api.services.tenders.pdf_format import _fill_word_blanks
from api.services.tenders.schema import BidBrief

engine.echo = False


async def main():
    async with AsyncSessionLocal() as db:
        raw = (await db.execute(select(TenderRecord.brief_json).order_by(
            TenderRecord.id.desc()
        ).limit(1))).scalar_one()
    brief = BidBrief.model_validate(raw)
    results = []
    for name in ('3d0a4ef456f5.format.docx', '68a42d038c39.format.docx'):
        source = Path('storage/tenders') / name
        data = source.read_bytes()
        doc = Document(source)
        before = [p.xml for p in doc.element.body.iter(qn('w:p'))]
        _fill_word_blanks(doc, brief)
        after = [p.xml for p in doc.element.body.iter(qn('w:p'))]
        first = doc.element.xml
        _fill_word_blanks(doc, brief)
        stable = first == doc.element.xml
        assert stable, name
        assert data == source.read_bytes()
        results.append(dict(file=name, paragraphs=len(before),
                            changed=sum(a != b for a, b in zip(before, after)),
                            repeatStable=stable, sourceUnchanged=True,
                            sourceSha256=hashlib.sha256(data).hexdigest()))
    Path('tmp/blank-spans/replay.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(json.dumps(results))
    await engine.dispose()


asyncio.run(main())
