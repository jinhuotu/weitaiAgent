import asyncio
import json
from hashlib import sha256
from pathlib import Path

from docx import Document

from api.services.tenders.assemble import assemble_bid_docx
from api.services.tenders.gen_biz_resp import business_requirements, draft_business_response
from api.services.tenders.schema import BidBrief, OutlineItem


class NoModel:
    async def complete(self, *args, **kwargs):
        raise AssertionError('Construction outline has no business response landing section; LLM must be skipped')


root = Path(__file__).resolve().parent
invitation_id = 'ff6d6a0b67aa'
invitation = Path('storage/tender-invitations') / f'{invitation_id}.txt'
source = invitation.with_name(f'{invitation_id}.format.docx')
before = sha256(source.read_bytes()).hexdigest()
brief = BidBrief(invitationId=invitation_id, generateVolume='business', layoutMode='outline',
                 autoDraftBizResp=True, attachQualifications=False, includePlaceholders=False,
                 outlineItems=[OutlineItem(id='letter', title='投标函', kind='letter'),
                               OutlineItem(id='plan', title='施工组织设计', kind='tech_chapter')])
requirements = business_requirements(brief, invitation.read_text(encoding='utf-8'))
assert not any('商务标得分等于' in r.text or '评审的业绩规定' in r.text for r in requirements)
prepared, notes = asyncio.run(draft_business_response(None, brief, client=NoModel()))
assert prepared.model_dump() == brief.model_dump()
assert any('【待补充】' in note for note in notes)
path, assembly_notes = assemble_bid_docx(prepared, root / 'actual-construction.business.docx',
                                       volume='business', catalog_slots=[], catalog_media={}, catalog_facts=[])
assert path.is_file() and Document(path).paragraphs
assert sha256(source.read_bytes()).hexdigest() == before
result = {'source': str(invitation), 'formatSourceUnchanged': True, 'llmCalls': 0,
          'businessClausesAfterFiltering': len(requirements), 'businessDraftNotes': notes,
          'generatedFile': str(path), 'generatedBytes': path.stat().st_size}
(root / 'actual-construction-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print('Actual saved construction text generated a business DOCX without 422; no LLM call; pending warning preserved; format source unchanged.')
