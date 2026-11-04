import asyncio
import json
from hashlib import sha256
from pathlib import Path

from docx import Document

from api.services.tenders.assemble import assemble_bid_docx
from api.services.tenders.gen_tech_plan import draft_technical_plan
from api.services.tenders.schema import BidBrief, OutlineItem

root = Path(__file__).resolve().parent
invitation_id = 'ff6d6a0b67aa'
source = Path('storage/tender-invitations') / f'{invitation_id}.format.docx'
original = sha256(source.read_bytes()).hexdigest()
results = []


class Client:
    def __init__(self, missing):
        self.missing = missing

    async def complete(self, *args, **kwargs):
        if self.missing:
            return '{"chapters": null}'
        return json.dumps({'chapters': [{'title': '施工组织设计',
                           'body': '技术方案正文示例：按招标要求核实施工条件，具体部署【待补充】。'}]}, ensure_ascii=False)


for volume in ('technical', 'full'):
    for missing in (True, False):
        brief = BidBrief(invitationId=invitation_id, generateVolume=volume, layoutMode='outline',
                         autoDraftTechPlan=True, attachQualifications=False, includePlaceholders=False,
                         outlineItems=[OutlineItem(id='letter', title='投标函', kind='letter'),
                                       OutlineItem(id='plan', title='施工组织设计', kind='tech_chapter')])
        prepared, notes = asyncio.run(draft_technical_plan(None, brief, client=Client(missing)))
        path, assembly_notes = assemble_bid_docx(
            prepared, root / f'actual-construction.{volume}.{"pending" if missing else "valid"}.docx',
            volume=volume, catalog_slots=[], catalog_media={}, catalog_facts=[])
        text = '\n'.join(p.text for p in Document(path).paragraphs)
        if missing:
            assert '【待补充】「施工组织设计」技术方案正文未完整生成' in text
        else:
            assert '技术方案正文示例：按招标要求核实施工条件' in text
        assert sha256(source.read_bytes()).hexdigest() == original
        results.append({'volume': volume, 'missingModelChapters': missing, 'file': str(path),
                        'generatedBytes': path.stat().st_size, 'notes': notes,
                        'sourceFormatUnchanged': True})
(root / 'actual-construction-results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
print('Actual saved construction format: technical/full DOCX generated for both missing and valid model output; expected body markers present; source unchanged. Model output was simulated.')
