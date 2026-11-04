import asyncio,json,hashlib
from pathlib import Path
from collections import Counter
from sqlalchemy import select
from db.session import AsyncSessionLocal,engine
from db.models.knowledge import KnowledgeDocument,KnowledgeIngestTask
from api.services.tenders.library_kb import _doc_file_path,key_from_tags

async def main():
 engine.echo=False
 async with AsyncSessionLocal() as db:
  docs=(await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.base_id==6))).scalars().all()
  tasks=(await db.execute(select(KnowledgeIngestTask).where(KnowledgeIngestTask.base_id==6))).scalars().all()
  byid={d.id:d for d in docs};bypid={d.public_id:d for d in docs}
  root=Path('storage');hashes={};scan=[]
  for folder in [root/'knowledge/tenderlib01',root/'tender-assets/slots']:
   for path in folder.rglob('*'):
    if path.is_file() and path.suffix.lower() in {'.pdf','.png','.jpg','.jpeg','.webp','.docx'}:
     digest=hashlib.sha256(path.read_bytes()).hexdigest();hashes.setdefault(digest,[]).append(str(path))
     doc=bypid.get(path.stem)
     scan.append({'path':str(path),'docId':doc.public_id if doc else '',
      'key':key_from_tags(doc.tags) if doc else '', 'linked':bool(doc and _doc_file_path(doc))})
  task_rows=[]
  for t in tasks:
   d=byid.get(t.document_id)
   task_rows.append({'task':t.public_id,'status':t.status,'doc':d.public_id if d else '',
    'source':d.source if d else '', 'key':key_from_tags(d.tags) if d else '',
    'parent':d.parent_id if d else '', 'hasFile':bool(d and _doc_file_path(d))})
  recover=[]
  for d in docs:
   matches=hashes.get(d.content_hash or '',[])
   if not _doc_file_path(d) and matches:
    recover.append({'doc':d.public_id,'key':key_from_tags(d.tags),'matches':matches})
  r={'tasks':task_rows,'disk':scan,'hashRecoveryCandidates':recover}
  Path('tmp/placement-structure/reconcile.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
  print(json.dumps({'taskFiles':dict(Counter(str(x['hasFile']) for x in task_rows)),'taskSources':dict(Counter(x['source'] for x in task_rows)),
   'disk':scan,'hashRecoveryCandidates':recover},ensure_ascii=True))
 await engine.dispose()
asyncio.run(main())
