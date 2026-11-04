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

ROOT = Path('tmp/general-tenders/legacy-replay')
ROOT.mkdir(parents=True, exist_ok=True)
SOURCE = Path('storage/tenders/3d0a4ef456f5.format.docx')
source_bytes = SOURCE.read_bytes()
source_doc = Document(SOURCE)
items = _read_toc_entries(toc_block_ids(source_doc), source_doc)
profile = json.loads(Path('tests/fixtures/legacy-company.json').read_text(encoding='utf-8'))
profile.update(invitationId='3d0a4ef456f5', layoutMode='outline', formatConfirmed=True,
                 outlineChapter='第八章 投标文件格式', generateVolume='full',
                 projectName='郑州高新技术产业开发区新能源充换电站项目', bidDate='2026-10-30',
                 includeCommitment=False, includePlaceholders=False, attachQualifications=False,
                 outlineItems=[OutlineItem(id=f'src{i}', title=r['title'], rawTitle=r['title'],
                                           level=r['level'], kind=classify_kind(r['title']))
                               for i,r in enumerate(items)])
brief = BidBrief.model_validate(profile)
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
    reports=[]
    for volume in ('business','technical','full'):
        b=brief.model_copy(deep=True); b.generateVolume=volume
        current,stats=run('current-'+volume,b,[],{})
        previous_path=Path('tmp/material-archives/replay')/('baseline-'+volume+'.docx')
        previous_doc=Document(previous_path)
        before=validate_native_toc(previous_doc)
        before.update(images=len(list(previous_doc.element.iter(qn('w:drawing')))),paragraphs=len(previous_doc.paragraphs))
        before_titles=[p.text.split('\t')[0] for p in previous_doc.paragraphs if 'PAGEREF' in p._p.xml]
        doc=Document(ROOT/('current-'+volume+'.docx'))
        after_titles=[p.text.split('\t')[0] for p in doc.paragraphs if 'PAGEREF' in p._p.xml]
        assert before_titles==after_titles, volume
        assert before['images']==stats['images'], (volume,before,stats)
        assert before['entries']==stats['entries'], (volume,before,stats)
        prior=package(previous_path)
        changed=[name for name in sorted(set(prior)|set(current)) if prior.get(name)!=current.get(name)]
        reports.append({'volume':volume,'before':before,'after':stats,'tocTitlesEqual':True,'scanCountEqual':True,'changedParts':changed})
    assert SOURCE.read_bytes()==source_bytes
    result={'sourceUnchanged':True,'cases':reports}
    (ROOT/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True))
asyncio.run(main())
