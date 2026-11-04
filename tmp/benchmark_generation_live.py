import asyncio,json,logging
from pathlib import Path
from time import perf_counter
from db.session import AsyncSessionLocal,engine
from api.services.models.runtime import build_llm_client
from api.services.tenders.schema import BidBrief
from api.services.tenders.gen_tech_plan import draft_technical_plan

logging.basicConfig(level=logging.WARNING)
logging.getLogger('api.services.tenders.generation_parallel').setLevel(logging.INFO)
engine.echo=False

async def main():
    data=json.loads(Path('tmp/benchmark-parse-result.json').read_text(encoding='utf-8'))
    brief=BidBrief.model_validate(data['brief'])
    brief.techPlanKbIds=[]
    brief.techPlanSourceIds=[]
    for item in brief.outlineItems:
        if item.kind in {'tech_plan','tech_chapter'}:
            item.draftBody='';item.draftStatus='empty'
    async with AsyncSessionLocal() as db:
        client=await build_llm_client(db,'pro')
    calls=0
    class CountClient:
        async def complete(self,*a,response_format=None,**kw):
            nonlocal calls
            calls+=1
            return await client.complete(*a,response_format=response_format,**kw)
    tick=perf_counter()
    result,notes=await draft_technical_plan(None,brief,client=CountClient())
    print(json.dumps({'seconds':perf_counter()-tick,'pro_calls':calls,'technical_bodies':sum(bool(i.draftBody) for i in result.outlineItems if i.kind in {'tech_plan','tech_chapter'})}),flush=True)
    Path('tmp/generation-live-result.json').write_text(result.model_dump_json(),encoding='utf-8')
    await engine.dispose()

asyncio.run(main())
