import asyncio, hashlib, json, sys
from pathlib import Path
sys.path[:0]=['apps','packages']
from sqlalchemy import select
from db.session import AsyncSessionLocal,engine
from db.models.tender import TenderRecord
from docx import Document
from docx.oxml.ns import qn
from api.services.tenders.schema import BidBrief
from api.services.tenders.assemble import assemble_bid_docx
from api.services.tenders.pdf_format import _word_format_file

root=Path('tmp/signature-label-fix/visual');root.mkdir(exist_ok=True)
async def main():
    engine.echo=False
    async with AsyncSessionLocal() as db:
        row=(await db.execute(select(TenderRecord).where(TenderRecord.docx_file=='0b5a28e7dc04.docx'))).scalar_one()
        brief=BidBrief.model_validate(row.brief_json)
        source=_word_format_file(brief.invitationId);before=hashlib.sha256(source.read_bytes()).hexdigest()
        report={}
        for volume in ['business','full']:
            output=root/(volume+'.docx')
            assemble_bid_docx(brief.model_copy(deep=True),output,volume=volume)
            d=Document(output)
            # Retain the complete preceding cover/TOC/body geometry while
            # truncating only after the appendix heading for fast visual QA.
            children=list(d.element.body)
            end=next(i for i,n in enumerate(children) if n.tag==qn('w:p') and ''.join(t.text or '' for t in n.iter(qn('w:t'))).strip()=='投标函附录')
            for n in children[end+1:]:
                if n.tag!=qn('w:sectPr'):d.element.body.remove(n)
            d.save(root/(volume+'-front.docx'))
            report[volume]={'frontFile':volume+'-front.docx'}
        report['sourceUnchanged']=hashlib.sha256(source.read_bytes()).hexdigest()==before
        (root.parent/'real-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report))
    await engine.dispose()
asyncio.run(main())
