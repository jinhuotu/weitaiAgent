import asyncio,json,logging,re
from pathlib import Path
from sqlalchemy import select
from db.session import AsyncSessionLocal,engine
from db.models.tender import TenderRecord
from api.services.tenders.schema import BidBrief
from api.services.tenders.library_kb import catalog_placeholders,resolve_attachments,library_facts
from api.services.tenders.company_materials import scoped_library_media
from api.services.tenders.placeholders import collect_slots
from api.services.tenders.section_materials import sections,_heading_text,_text,section_type
from api.services.tenders.pdf_format import toc_block_ids
from docx import Document
from docx.oxml.ns import qn
import api.services.tenders.generate as generation

logging.disable(logging.CRITICAL);engine.echo=False
async def main():
    async with AsyncSessionLocal() as db:
        record=(await db.execute(select(TenderRecord).where(TenderRecord.docx_file=='46ab84f55c45.docx'))).scalars().first()
        if record is None:
            from api.services.tenders.pdf_format import _word_format_file
            candidates=(await db.execute(select(TenderRecord).where(TenderRecord.brief_json.is_not(None)).order_by(TenderRecord.id.desc()).limit(20))).scalars().all()
            record=next((row for row in candidates if _word_format_file((row.brief_json or {}).get('invitationId','')) is not None),None)
        if record is None:
            raise RuntimeError('No generation record with a readable original format template')
        brief=BidBrief.model_validate(record.brief_json)
        catalog=await catalog_placeholders(db)
        slots=collect_slots(brief.extraPlaceholders,catalog=catalog,include_keys=brief.includeSlotKeys,outline_items=brief.outlineItems)
        media=await resolve_attachments(db,slots)
        media,allowed,_,_=await scoped_library_media(db,brief,media)
        facts=[f for f in await library_facts(db) if f.get('fileId') in allowed]
        await db.rollback()
    folder=Path('tmp/owned-section-output').resolve();folder.mkdir(exist_ok=True)
    generation.tenders_output_dir=lambda:folder
    brief.generateVolume='business';brief.generateBoth=False
    result=generation.generate_bid(brief,catalog_slots=slots,catalog_media=media,catalog_facts=facts)
    path=folder/result['docxFile'];doc=Document(path)
    flow=[]
    for head,body,_ in sections(doc,set(toc_block_ids(doc))):
        images=sum(len(list(el.iter(qn('w:drawing')))) for el in body)
        boxes=sum('此处粘贴' in _text(el) for el in body)
        flow.append({'chapter':_heading_text(head),'type':section_type(head,body),'images':images,'placeholders':boxes})
    from api.services.tenders.pdf_format import _blank_text
    dates=[]
    for head,body,_ in sections(doc,set(toc_block_ids(doc))):
        for block in body:
            for paragraph in block.iter(qn('w:p')):
                text=_blank_text(paragraph)
                if re.search(r'年[^。\n]{0,12}月[^。\n]{0,12}日',text) and len(text)<100:
                    dates.append({'chapter':_heading_text(head),'text':text,
                                  'filled':bool(re.search(r'\d{4}年\d{1,2}月\d{1,2}日',text))})
    report={'output':str(path),'flow':flow,'date_slots':dates}
    Path('tmp/owned-section-flow.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True),flush=True)
    await engine.dispose()
asyncio.run(main())
