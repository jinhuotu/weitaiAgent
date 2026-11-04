import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from docx import Document
from docx.oxml.ns import qn
from api.services.tenders.document import _pageref_bookmark
from api.services.tenders.generate import generate_bid
from api.services.tenders.native_format import ensure_toc_rows, validate_native_toc
from api.services.tenders.schema import BidBrief, PlaceholderItem

source = Path('storage/tenders/bbdacfe6c5ad.format.docx').resolve()
before = hashlib.sha256(source.read_bytes()).hexdigest()
audit = []
for volume in ('business', 'technical', 'full'):
    folder = Path('tmp/toc-root-audit') / volume
    folder.mkdir(exist_ok=True)
    brief = BidBrief(invitationId='audit-word', layoutMode='outline',
                     outlineChapter='第八章 投标文件格式', generateVolume=volume,
                     requiredSlotKeys=['social'], includeSlotKeys=['social'],
                     attachQualifications=False, bidderName='目录结构回归核验')
    with patch('api.services.tenders.pdf_format._word_format_file', return_value=source), patch(
        'api.services.tenders.generate.tenders_output_dir', return_value=folder
    ):
        result = generate_bid(brief, catalog_slots=[PlaceholderItem(
            key='social', title='社保缴纳证明', attachToSection='资格审查其他材料'
        )], catalog_media={})
        dest = folder / result['techDocxFile' if volume == 'technical' else 'docxFile']
        doc = Document(dest)
        stats = validate_native_toc(doc)
        rows = [p for p in doc.paragraphs if _pageref_bookmark(p)]
        stats.update(volume=volume, path=str(dest.resolve()), titles=[p.text for p in rows])
        once = doc.element.xml
        ensure_toc_rows(doc, brief, volume=volume)
        stats['repeatStable'] = doc.element.xml == once
        names = [n.get(qn('w:name')) for n in doc.element.iter(qn('w:bookmarkStart'))]
        stats['uniqueBookmarks'] = len(names) == len(set(names))
        audit.append(stats)
        print(volume, stats['entries'], 'repeatStable', stats['repeatStable'], flush=True)
try:
    validate_native_toc(Document('storage/tenders/447abadc96e9.docx'))
    broken_rejected = False
except Exception as exc:
    broken_rejected = True
    print('original_broken_output_rejected', type(exc).__name__, flush=True)
report = dict(volumes=audit, brokenOutputRejected=broken_rejected,
              sourceUnchanged=before == hashlib.sha256(source.read_bytes()).hexdigest())
Path('tmp/toc-root-audit/replay.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
