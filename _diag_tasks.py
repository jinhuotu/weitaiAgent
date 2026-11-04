from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import text

from db.session import AsyncSessionLocal


async def main() -> None:
    async with AsyncSessionLocal() as db:
        print("=== ingest task status summary ===")
        rows = (
            await db.execute(
                text(
                    "SELECT status, COUNT(*) AS n FROM knowledge_ingest_tasks "
                    "GROUP BY status ORDER BY status"
                )
            )
        ).all()
        for r in rows:
            print(dict(r._mapping))

        print("=== queued / parsing / failed tasks (latest 20) ===")
        rows = (
            await db.execute(
                text(
                    "SELECT t.id, t.public_id, t.status, t.progress, t.error_msg, "
                    "t.document_id, d.name AS doc_name, t.created_at, t.updated_at "
                    "FROM knowledge_ingest_tasks t "
                    "LEFT JOIN knowledge_documents d ON d.id = t.document_id "
                    "WHERE t.status IN ('queued','parsing','failed') "
                    "ORDER BY t.id DESC LIMIT 20"
                )
            )
        ).all()
        for r in rows:
            m = dict(r._mapping)
            for k, v in list(m.items()):
                if hasattr(v, "strftime"):
                    m[k] = v.strftime("%Y-%m-%d %H:%M:%S")
            print(m)


if __name__ == "__main__":
    asyncio.run(main())
