"""Replay the same real invitation through opt-in argument adapters, offline."""
import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zipfile import ZipFile
from io import BytesIO
from types import SimpleNamespace

from docx import Document
from docx.oxml.ns import qn
from PIL import Image
from api.services.tenders import material_store as store
from api.services.tenders.assemble import assemble_bid_docx
from api.services.tenders.native_format import _read_toc_entries, validate_native_toc
from api.services.tenders.pdf_format import toc_block_ids
from api.services.tenders.outline import classify_kind
from api.services.tenders.schema import BidBrief, MaterialUse, OutlineItem

ROOT = Path('tmp/material-archives/replay')
ROOT.mkdir(parents=True, exist_ok=True)
SOURCE = Path('storage/tenders/3d0a4ef456f5.format.docx')
source_bytes = SOURCE.read_bytes()
source_doc = Document(SOURCE)
items = _read_toc_entries(toc_block_ids(source_doc), source_doc)
brief = BidBrief(invitationId='3d0a4ef456f5', layoutMode='outline', formatConfirmed=True,
                 outlineChapter='第八章 投标文件格式', generateVolume='full',
                 projectName='郑州高新技术产业开发区新能源充换电站项目', bidDate='2026-10-30',
                 includeCommitment=False, includePlaceholders=False, attachQualifications=False,
                 outlineItems=[OutlineItem(id=f'src{i}', title=r['title'], rawTitle=r['title'],
                                           level=r['level'], kind=classify_kind(r['title']))
                               for i,r in enumerate(items)])
target = next(t for t in store.existing_sections(brief) if t == '六、项目管理机构')


def package(path):
    with ZipFile(path) as z:
        return {n: hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist()}


def run(name, b, slots, media):
    path = ROOT / (name + '.docx')
    _, notes = assemble_bid_docx(b, path, catalog_slots=slots, catalog_media=media,
                      catalog_facts=[], volume=b.generateVolume)
    (ROOT / (name + '.notes.json')).write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding='utf-8')
    doc = Document(path)
    stats = validate_native_toc(doc)
    stats.update(images=len(list(doc.element.iter(qn('w:drawing')))),
                 paragraphs=len(doc.paragraphs))
    return package(path), stats


async def main():
    baseline = {}
    for volume in ('business', 'technical', 'full'):
        b = brief.model_copy(deep=True)
        b.generateVolume = volume
        baseline[volume] = run('baseline-' + volume, b, [], {})
    out = BytesIO()
    Image.new('RGB', (320, 180), 'white').save(out, format='PNG')
    reports = []
    archives = ROOT / 'isolated-store'
    with patch.object(store, 'tender_assets_dir', return_value=archives), patch(
        'api.services.knowledge.parsers.extract_text_from_file',
        AsyncMock(return_value=SimpleNamespace(text=brief.projectName))
    ):
        for category, (_, _, subtypes) in store.CATEGORIES.items():
            item = await store.upload_material(1, brief.invitationId, category,
                       filename='测试资料.png', data=out.getvalue(), entity='本次测试', subtype=subtypes[0])
            entry = {'category': category, 'off': {}}
            for volume in ('business', 'technical', 'full'):
                b = brief.model_copy(deep=True)
                b.generateVolume = volume
                b.materialSelections = {category: MaterialUse(enabled=False, fileIds=[item['id']], targetSection=target)}
                slots, media, notes, refs = store.generation_inputs(1, b, [], {})
                actual, stats = run(f'off-{category}-{volume}', b, slots, media)
                assert actual == baseline[volume][0], (category, volume)
                assert stats == baseline[volume][1]
                assert not notes and not refs
                entry['off'][volume] = {'packagePartsEqual': True, **stats}
            b = brief.model_copy(deep=True)
            b.materialSelections = {category: MaterialUse(enabled=True, fileIds=[item['id']], targetSection=target)}
            slots, media, _, _ = store.generation_inputs(1, b, [], {})
            _, stats = run('on-' + category, b, slots, media)
            assert stats['entries'] == baseline['full'][1]['entries']
            assert stats['images'] == baseline['full'][1]['images'] + 1, category
            entry['on'] = stats
            reports.append(entry)
    assert SOURCE.read_bytes() == source_bytes
    result = {'sourceSha256':hashlib.sha256(source_bytes).hexdigest(),
              'sourceUnchanged':True, 'categories':reports}
    (ROOT / 'comparison.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'categories': len(reports), 'offComparisons':len(reports)*3,
                      'allOffPackagePartsEqual': True, 'onTocCountStable':True,
                      'onAddsExactlyOneImage':True, 'sourceUnchanged':True}))


asyncio.run(main())
