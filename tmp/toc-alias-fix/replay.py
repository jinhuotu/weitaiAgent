import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from docx import Document
from docx.oxml.ns import qn
from api.services.tenders.document import _pageref_bookmark
from api.services.tenders.format_rules import peel_toc_lead
from api.services.tenders.generate import generate_bid
from api.services.tenders.native_format import _read_toc_entries, ensure_toc_rows, validate_native_toc
from api.services.tenders.outline import classify_kind
from api.services.tenders.pdf_format import toc_block_ids
from api.services.tenders.schema import BidBrief, OutlineItem, PlaceholderItem

source = Path('storage/tenders/43e52bfb9ba9.format.docx').resolve()
digest = hashlib.sha256(source.read_bytes()).hexdigest()
source_doc = Document(source)
rows = _read_toc_entries(toc_block_ids(source_doc), source_doc)
results = []
for volume, anonymous in [('business', False), ('technical', False), ('technical', True), ('full', False)]:
    folder = Path('tmp/toc-alias-fix') / f'{volume}-{anonymous}'
    folder.mkdir(exist_ok=True)
    items = []
    for i, row in enumerate(rows):
        title = peel_toc_lead(row['title'])
        kind = 'tech_plan' if title == '施工组织格式' else classify_kind(title)
        items.append(OutlineItem(id=f'o{i+1:02d}', title=title, rawTitle=row['title'],
                                 kind=kind, level=row['level'],
                                 draftBody='已确认的本项目施工技术响应正文。' if title == '施工组织格式' else ''))
    brief = BidBrief(invitationId='alias-audit', outlineChapter='第八章 投标文件格式',
                     layoutMode='outline', generateVolume=volume, techAnonymous=anonymous,
                     autoDraftTechPlan=True, outlineItems=items,
                     requiredSlotKeys=['social'], includeSlotKeys=['social'],
                     attachQualifications=False)
    with patch('api.services.tenders.pdf_format._word_format_file', return_value=source), patch(
        'api.services.tenders.generate.tenders_output_dir', return_value=folder
    ):
        result = generate_bid(brief, catalog_slots=[PlaceholderItem(
            key='social', title='社保缴纳证明', attachToSection='资格审查其他材料'
        )], catalog_media={})
        path = folder / result['techDocxFile' if volume == 'technical' else 'docxFile']
        doc = Document(path)
        stats = validate_native_toc(doc)
        assert not any('原版格式章缺少「施工组织格式」' in note for note in result['warnings'])
        if volume != 'business':
            heading = next(p for p in doc.paragraphs if p.text == '施工组织设计')
            row = next(p for p in doc.paragraphs if p.text.startswith('十、施工组织格式\t'))
            assert any(n.get(qn('w:name')) == _pageref_bookmark(row)
                       for n in heading._p.iter(qn('w:bookmarkStart')))
            assert sum('已确认的本项目施工技术响应正文。' in p.text for p in doc.paragraphs) == 1
        once = doc.element.xml
        ensure_toc_rows(doc, brief, volume=volume)
        stats.update(volume=volume, anonymous=anonymous, repeatStable=once == doc.element.xml,
                     path=str(path.resolve()))
        assert stats['repeatStable']
        results.append(stats)
        print(json.dumps(stats), flush=True)
assert digest == hashlib.sha256(source.read_bytes()).hexdigest()
Path('tmp/toc-alias-fix/replay.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
