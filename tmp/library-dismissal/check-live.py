import asyncio
import json
import sys
from pathlib import Path

sys.path[:0] = [str(Path.cwd() / 'apps'), str(Path.cwd() / 'packages')]
from db.session import AsyncSessionLocal, engine
from api.services.tenders.library_kb import list_slots_status_kb, dismissed_library_keys


async def main():
    engine.echo = False
    async with AsyncSessionLocal() as db:
        rows = await list_slots_status_kb(db)
        keys = [row['key'] for row in rows]
        print(json.dumps({'rows': len(rows), 'uniqueKeys': len(set(keys)),
                          'licenseRows': keys.count('license'),
                          'dismissals': len(await dismissed_library_keys(db)),
                          'licenseFileCount': sum(row['fileCount'] for row in rows if row['key'] == 'license')}, ensure_ascii=False))
    await engine.dispose()


asyncio.run(main())
