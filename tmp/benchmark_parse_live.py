import asyncio
import json
import logging
from pathlib import Path
from time import perf_counter
from fastapi import UploadFile
from db.session import AsyncSessionLocal, engine
from api.services.tenders.extract import parse_invitation

logging.basicConfig(level=logging.WARNING)
logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
engine.echo = False
for name in ('api.services.tenders.extract_chunks_optimized', 'api.services.tenders.material_intents', 'api.services.tenders.performance_tracker'):
    logging.getLogger(name).setLevel(logging.INFO)

async def main():
    source = Path('storage/tender-invitations/43aedb02d64a.docx')
    tick = perf_counter()
    async with AsyncSessionLocal() as db:
        db.commit = db.flush  # Rollback-only database benchmark.
        with source.open('rb') as stream:
            result = await parse_invitation(db, UploadFile(filename=source.name, file=stream), persist_library=True)
        await db.rollback()
    elapsed = perf_counter()-tick
    Path('tmp/benchmark-parse-result.json').write_text(json.dumps(result, ensure_ascii=False, default=str), encoding='utf-8')
    print(json.dumps({'seconds': elapsed, 'result_keys': list(result)}, ensure_ascii=False), flush=True)
    await engine.dispose()

asyncio.run(main())
