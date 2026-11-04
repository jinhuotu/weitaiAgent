from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db.session import AsyncSessionLocal
from api.services.tenders.library_kb import (
    get_base_by_public_id,
    TENDER_LIB_PUBLIC_ID,
    _list_docs,
    _doc_file_path,
    key_from_tags,
    legal_persons_from_library,
)
from api.services.tenders.company_identity import (
    company_from_document,
    credit_code_from_document,
)
from api.services.tenders.id_card import load_id_meta


async def main() -> None:
    async with AsyncSessionLocal() as db:
        base = await get_base_by_public_id(db, TENDER_LIB_PUBLIC_ID)
        docs = await _list_docs(db, base.id)
        print("TOTAL docs:", len(docs))
        for doc in docs:
            key = key_from_tags(doc.tags)
            if key in ("id_legal", "id_agent"):
                path = _doc_file_path(doc)
                meta = load_id_meta(doc.summary) or {}
                print("---")
                print("file_id:", doc.public_id)
                print("key:", key)
                print("path:", path)
                print("path exists:", path.exists() if path else None)
                print("owner:", repr(company_from_document(doc)))
                print("code:", repr(credit_code_from_document(doc)))
                print(
                    "meta:",
                    {
                        "personKey": meta.get("personKey"),
                        "idNo": meta.get("idNo"),
                        "name": meta.get("name"),
                        "side": meta.get("side"),
                    },
                )
        print("=== persons (full, unscoped) ===")
        persons = await legal_persons_from_library(db, extract_missing=False)
        for p in persons:
            print(
                {
                    "key": p.get("key"),
                    "name": p.get("name"),
                    "idNo": p.get("idNo"),
                    "fileIds": p.get("fileIds"),
                    "sides": p.get("sides"),
                }
            )


if __name__ == "__main__":
    asyncio.run(main())
