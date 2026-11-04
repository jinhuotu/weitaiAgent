import asyncio,json,sys
from pathlib import Path
sys.path[:0]=['apps','packages']
from db.session import AsyncSessionLocal,engine
from api.services.tenders.schema import BidBrief
from api.services.tenders.technical_package import draft_package
from api.services.tenders.assemble import assemble_bid_docx

root=Path('tmp/technical-plan-repair')
async def main():
    engine.echo=False
    brief=BidBrief.model_validate(json.loads((root/'baseline.json').read_text(encoding='utf-8'))['brief'])
    async with AsyncSessionLocal() as db:
        print('Drafting missing original points and four resource tables',flush=True)
        package=json.loads((root/'real-package.json').read_text(encoding='utf-8')); notes=[]
        (root/'real-package.json').write_text(json.dumps(package,ensure_ascii=False,indent=2),encoding='utf-8')
        brief.technicalPackage=package
        for anon in [False,True]:
            b=brief.model_copy(deep=True);b.techAnonymous=anon
            path=root/('real-anonymous.docx' if anon else 'real-named.docx')
            _,warnings=assemble_bid_docx(b,path,volume='technical',catalog_slots=[],catalog_media={})
            print('Assembled',path.name,len(package['sections']),len(package['tables']),len(warnings),flush=True)
        (root/'notes.json').write_text(json.dumps(notes,ensure_ascii=False,indent=2),encoding='utf-8')
    await engine.dispose()
asyncio.run(main())
