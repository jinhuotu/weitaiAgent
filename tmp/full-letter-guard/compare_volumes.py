import asyncio
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from sqlalchemy import select
from db.session import AsyncSessionLocal, engine
from db.models.tender import TenderRecord
from api.services.tenders.generate import generate_bid
from api.services.tenders.schema import BidBrief

engine.echo = False


async def main():
    async with AsyncSessionLocal() as db:
        raw = (await db.execute(select(TenderRecord.brief_json).where(
            TenderRecord.docx_file == '1192bcbc9bbb.docx'
        ))).scalar_one()
    report = {}
    for volume in ('business', 'technical'):
        folder = Path('tmp/full-letter-guard') / sys.argv[1] / volume
        folder.mkdir(parents=True, exist_ok=True)
        brief = BidBrief.model_validate(raw)
        brief.generateVolume = volume
        with patch('api.services.tenders.generate.tenders_output_dir', return_value=folder):
            result = generate_bid(brief, catalog_slots=[], catalog_media={})
        docx = folder / result['techDocxFile' if volume == 'technical' else 'docxFile']
        with ZipFile(docx) as archive:
            parts = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()}
        report[volume] = dict(parts=parts, warnings=result['warnings'])
        print(volume, 'parts', len(parts), flush=True)
    Path(f'tmp/full-letter-guard/{sys.argv[1]}.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    await engine.dispose()


asyncio.run(main())
