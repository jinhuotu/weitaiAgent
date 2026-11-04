import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from docx import Document
from docx.oxml.ns import qn
from sqlalchemy import select
from db.session import AsyncSessionLocal, engine
from db.models.knowledge import KnowledgeBase, KnowledgeDocument
from api.services.tenders.generate import generate_bid
from api.services.tenders.library_kb import key_from_tags, resolve_attachments
from api.services.tenders.native_format import validate_native_toc
from api.services.tenders.scan_placement import _prefix, place_library_scans
from api.services.tenders.schema import BidBrief, PlaceholderItem

engine.echo = False


async def main():
    slots = [PlaceholderItem(key=key, title='营业执照')
             for key in ('m5641425fda52', 'license')]
    async with AsyncSessionLocal() as db:
        # Resolve performs read-only queries; prevent legacy-directory creation.
        with patch('api.services.tenders.library_kb.list_slot_files', return_value=[]):
            media = await resolve_attachments(db, slots)
    assert media[slots[0].key] is media['license']
    assert len(media['license']) == 1
    source = Path('storage/tenders/2d973e3699b4.format.docx').resolve()
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    report = []
    for volume in ('business', 'full'):
        folder = Path('tmp/material-alias-dedup') / volume
        folder.mkdir(exist_ok=True)
        brief = BidBrief(invitationId='dedup-audit', layoutMode='outline',
                         outlineChapter='第八章 投标文件格式', generateVolume=volume,
                         attachQualifications=False, requiredSlotKeys=['license'],
                         includeSlotKeys=['license', slots[0].key])
        with patch('api.services.tenders.pdf_format._word_format_file', return_value=source), patch(
            'api.services.tenders.generate.tenders_output_dir', return_value=folder
        ):
            result = generate_bid(brief, catalog_slots=slots, catalog_media=media)
        doc = Document(folder / result['docxFile'])
        stats = validate_native_toc(doc)
        count = sum(len(list(p.iter(qn('w:drawing')))) for p in doc.element.iter(qn('w:p'))
                    if any(b.get(qn('w:name'), '').startswith(_prefix('license'))
                           for b in p.iter(qn('w:bookmarkStart'))))
        assert count == 1
        once = doc.element.xml
        place_library_scans(doc, brief, media, slots)
        assert doc.element.xml == once
        stats.update(volume=volume, licenseImages=count, repeatStable=True)
        report.append(stats)
        print(json.dumps(stats), flush=True)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    Path('tmp/material-alias-dedup/replay.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    await engine.dispose()


asyncio.run(main())
