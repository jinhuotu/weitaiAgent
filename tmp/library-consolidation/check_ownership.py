import asyncio,json,sys,hashlib,re
from pathlib import Path
sys.path[:0]=[str(Path.cwd()/'apps'),str(Path.cwd()/'packages')]
from sqlalchemy import select
from db.session import engine,AsyncSessionLocal
from db.models.knowledge import KnowledgeDocument,KnowledgeBase
from api.services.tenders import library_kb
from api.services.tenders.facts import load_facts
from api.services.tenders.company_identity import company_from_document,credit_code_from_document,COMPANY_RE
from api.services.knowledge.ingest import read_extracted_text

async def main():
 engine.echo=False
 async with AsyncSessionLocal() as db:
  base=(await db.execute(select(KnowledgeBase).where(KnowledgeBase.public_id=='tenderlib01'))).scalar_one()
  docs=(await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.base_id==base.id))).scalars().all()
  rows=[]
  for d in docs:
   if not library_kb._doc_file_path(d):continue
   facts=load_facts(d.summary or '')
   raw=read_extracted_text(base.public_id,d.public_id) or ''
   row={'key':library_kb.key_from_tags(d.tags),'id':d.public_id,'status':d.status,
    'summaryChars':len(d.summary or ''),'facts':bool(facts),'fieldNames':[f['name'] for f in (facts or {}).get('fields',[])],
    'owner':bool(company_from_document(d)),'code':bool(credit_code_from_document(d)),
    'bidderTag':any(str(t).startswith('bidder:') for t in (d.tags or [])),
    'error':bool(d.error_msg),'rawChars':len(raw),'ownerLabels':re.findall(r'企业名称|公司名称|存款人名称|单位名称|账户名称|开户名称|供应商名称|主体名称',raw),
    'candidateHashes':[hashlib.sha256(name.encode()).hexdigest()[:8] for name in COMPANY_RE.findall(raw)]}
   rows.append(row)
  Path('tmp/library-consolidation/ownership.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
  print(json.dumps(rows,ensure_ascii=False))
 await engine.dispose()
asyncio.run(main())
