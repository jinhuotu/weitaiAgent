import asyncio,json
from pathlib import Path
from sqlalchemy import select
from db.session import AsyncSessionLocal,engine
from db.models.tender import TenderRecord
from api.services.tenders import library_kb as kb
from api.services.tenders.company_materials import document_matches_company
from api.services.tenders.company_identity import company_from_document,credit_code_from_document
from api.services.tenders.facts import load_facts
from api.services.tenders.schema import BidBrief

async def main():
    engine.echo=False
    report={}
    try:
        async with AsyncSessionLocal() as db:
            record=(await db.execute(select(TenderRecord).order_by(TenderRecord.id.desc()).limit(1))).scalar_one_or_none()
            brief=BidBrief.model_validate(record.brief_json or {}) if record else BidBrief()
            base=await kb.get_base_by_public_id(db,kb.TENDER_LIB_PUBLIC_ID)
            docs=await kb._list_docs(db,base.id); by_id={d.public_id:d for d in docs}
            rows=[]
            for d in docs:
                path=kb._doc_file_path(d)
                parent=by_id.get(d.parent_id)
                facts=load_facts(d.summary or '') or {}
                rows.append({'id':d.public_id,'key':kb.key_from_tags(d.tags),
                    'parentKey':kb.key_from_tags(parent.tags) if parent else '',
                    'readable':path is not None,'hasFileKey':bool(d.file_key),'hasStoragePath':bool(d.storage_path),
                    'status':d.status,'summaryPrefix':(d.summary or '')[:12],
                    'tagPrefixes':sorted({str(t).split(':',1)[0] for t in d.tags or []}),
                    'ownerPresent':bool(company_from_document(d)), 'codePresent':bool(credit_code_from_document(d)),
                    'fields':[f.get('name') for f in facts.get('fields',[])],
                    'matches':document_matches_company(d,brief,key=kb.key_from_tags(d.tags))})
            report={'docs':len(docs),'files':rows,'bidderPresent':bool(brief.bidderName)}
    except Exception as exc:report={'errorType':type(exc).__name__}
    finally:await engine.dispose()
    Path('tmp/placement-structure/diagnostic.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True))
asyncio.run(main())
