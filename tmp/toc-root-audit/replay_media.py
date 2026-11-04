import json
from pathlib import Path
from unittest.mock import patch
from docx import Document
from docx.oxml.ns import qn
from PIL import Image
from api.services.tenders.generate import generate_bid
from api.services.tenders.native_format import ensure_toc_rows, validate_native_toc
from api.services.tenders.schema import BidBrief, PlaceholderItem

root = Path('tmp/toc-root-audit')
scan = root / 'social-test-scan.png'
Image.new('RGB', (420, 594), 'white').save(scan)
report = []
for volume in ('business', 'full'):
    folder = root / ('media-' + volume)
    folder.mkdir(exist_ok=True)
    brief = BidBrief(invitationId='audit-word', layoutMode='outline',
                     outlineChapter='第八章 投标文件格式', generateVolume=volume,
                     requiredSlotKeys=['social'], includeSlotKeys=['social'],
                     attachQualifications=False)
    with patch('api.services.tenders.pdf_format._word_format_file', return_value=Path(
        'storage/tenders/bbdacfe6c5ad.format.docx').resolve()), patch(
        'api.services.tenders.generate.tenders_output_dir', return_value=folder
    ):
        result = generate_bid(brief, catalog_slots=[PlaceholderItem(
            key='social', title='社保缴纳证明', attachToSection='资格审查其他材料'
        )], catalog_media={'social': [scan]})
        dest = folder / result['docxFile']
        doc = Document(dest)
        stats = validate_native_toc(doc)
        children = list(doc.element.body)
        drawings = [i for i, el in enumerate(children) if 'scan_slot_' in el.xml
                    and next(el.iter(qn('w:drawing')), None) is not None]
        assert len(drawings) == 1 and min(drawings) > stats['firstBodyBlock']
        once = doc.element.xml
        ensure_toc_rows(doc, brief, volume=volume)
        stats.update(volume=volume, drawingBlocks=drawings, repeatStable=once == doc.element.xml)
        report.append(stats)
        print(json.dumps(stats), flush=True)
(root / 'replay-media.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
