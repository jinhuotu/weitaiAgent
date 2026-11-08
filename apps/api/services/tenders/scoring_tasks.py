"""In-process background scoring tasks for invitation parsing."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

_TASKS: dict[str, dict] = {}


def create_scoring_task(invitation_id: str, scope: dict, *, ocr_capped: bool = False) -> str:
    task_id = uuid4().hex
    _TASKS[task_id] = {"id": task_id, "status": "queued", "result": None, "error": None,
                       "createdAt": datetime.now(timezone.utc).isoformat()}
    asyncio.create_task(_run(task_id, invitation_id, scope, ocr_capped))
    return task_id


async def _run(task_id: str, invitation_id: str, scope: dict, ocr_capped: bool) -> None:
    state = _TASKS.get(task_id)
    if not state:
        return
    state["status"] = "running"
    try:
        from db.session import AsyncSessionLocal
        from api.services.tender_comparison import extract_scoring
        from api.services.tenders.assets import load_invitation_text
        async with AsyncSessionLocal() as db:
            result = await asyncio.wait_for(
                extract_scoring(db, load_invitation_text(invitation_id), scope=scope), timeout=180
            )
        if ocr_capped:
            result["complete"] = result["extractionComplete"] = False
            result["phase"] = "partial"
            result.setdefault("warnings", []).append("OCR reached its page limit; source review is required.")
        state.update(status="completed", result=result)
    except asyncio.TimeoutError:
        state.update(status="failed", error="评分识别超过180秒，已转为人工复核")
    except Exception as exc:  # noqa: BLE001
        state.update(status="failed", error=str(exc)[:500])


def get_scoring_task(task_id: str) -> dict | None:
    return _TASKS.get(task_id)
