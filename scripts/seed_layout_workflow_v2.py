"""种子：案例知识库 + 提示词库 + 发布「充电站布置 v2」，并下线旧「充电站平面布置」。

幂等：同名知识库/工作流已存在则更新。历史图纸请按 scripts/kb_cases 模板做成 Markdown 再入库；
PDF/截图用「图纸附件」挂到对应案例卡，不要 OCR 进向量。

用法：
  python scripts/seed_layout_workflow_v2.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT / "apps"))

from sqlalchemy import select

from api.services.knowledge import bases as bases_svc
from api.services.knowledge.ingest import ingest_text
from api.services.layouts.v2.graph import (
    KB_NAME,
    RETIRED_WORKFLOW_NAME,
    WORKFLOW_NAME,
    WORKFLOW_REMARK,
    default_layout_v2_graph,
)
from api.services.layouts.v2.prompts import PROMPT_SEEDS
from api.services.prompts import configs as prompt_svc
from api.services.workflows import crud as crud_svc
from db.models.knowledge import KnowledgeBase
from db.models.prompt import Prompt
from db.models.workflow import Workflow
from db.session import AsyncSessionLocal

CASES_DIR = ROOT / "scripts" / "kb_cases"


async def _ensure_prompts(db) -> None:  # noqa: ANN001
    for name, content, remark in PROMPT_SEEDS:
        result = await db.execute(select(Prompt).where(Prompt.name == name).limit(1))
        row = result.scalar_one_or_none()
        if row is None:
            await prompt_svc.create_config(db, name=name, content=content, remark=remark)
            print(f"[ok] created prompt: {name}")
        else:
            await prompt_svc.update_config(
                db,
                public_id=row.public_id,
                content=content,
                remark=remark,
                enabled=True,
            )
            print(f"[ok] updated prompt: {name}")


async def _ensure_case_kb(db) -> str:  # noqa: ANN001
    result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.name == KB_NAME).limit(1))
    row = result.scalar_one_or_none()
    if row is not None:
        print(f"[info] knowledge base exists: {KB_NAME} id={row.public_id}")
        return row.public_id
    item = await bases_svc.create_base(
        db,
        name=KB_NAME,
        description="充电站布置历史案例（Markdown 案例卡片，不是原图 OCR）",
        purpose="rag",
    )
    print(f"[ok] created knowledge base: {KB_NAME} id={item['id']}")
    return str(item["id"])


async def _ingest_case_files(db, kb_id: str) -> int:  # noqa: ANN001
    if not CASES_DIR.is_dir():
        print(f"[warn] cases dir missing: {CASES_DIR}")
        return 0
    n = 0
    for path in sorted(CASES_DIR.glob("*.md")):
        if "模板" in path.stem:
            continue
        text = path.read_text(encoding="utf-8").strip()
        if len(text) < 8:
            continue
        item = await ingest_text(
            db,
            base_public_id=kb_id,
            name=path.stem,
            content=text,
            source="text",
            file_type="md",
            tags=["充电站", "布置案例"],
            uploader="seed",
        )
        status = item.get("status") or ""
        print(f"[ok] case {path.name} status={status} id={item.get('id')}")
        n += 1
    return n


async def _retire_legacy_layout_workflow(db, *, keep_public_id: str) -> None:  # noqa: ANN001
    result = await db.execute(select(Workflow).where(Workflow.name == RETIRED_WORKFLOW_NAME))
    rows = [row for row in result.scalars().all() if row.public_id != keep_public_id]
    if not rows:
        print(f"[info] no legacy workflow: {RETIRED_WORKFLOW_NAME}")
        return
    for row in rows:
        public_id = row.public_id
        await crud_svc.delete_workflow(db, public_id=public_id)
        print(f"[ok] retired workflow: {RETIRED_WORKFLOW_NAME} id={public_id}")


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        await _ensure_prompts(db)
        kb_id = await _ensure_case_kb(db)
        try:
            n = await _ingest_case_files(db, kb_id)
            print(f"[info] ingested {n} layout case(s) into {KB_NAME} (not auto-loaded)")
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] skip case ingest ({exc})")

        graph = default_layout_v2_graph(knowledge_base_ids=[kb_id] if kb_id else [])
        result = await db.execute(select(Workflow).where(Workflow.name == WORKFLOW_NAME).limit(1))
        row = result.scalar_one_or_none()
        if row is None:
            item = await crud_svc.create_workflow(
                db,
                name=WORKFLOW_NAME,
                remark=WORKFLOW_REMARK,
                domain="ai",
                enabled=True,
            )
            public_id = str(item["id"])
            print(f"[ok] created workflow: {WORKFLOW_NAME} id={public_id}")
        else:
            public_id = row.public_id
            print(f"[info] workflow exists: {WORKFLOW_NAME} id={public_id}")

        await crud_svc.save_graph(db, public_id=public_id, graph=graph)
        item = await crud_svc.publish_workflow(
            db,
            public_id=public_id,
            changelog="v2：条件表 + 读图 + JSON + 程序出图 + 一次校验修补",
        )
        if item.get("enabled") is False:
            item = await crud_svc.update_workflow(db, public_id=public_id, enabled=True)
        published = item.get("publishedVersion") or {}
        print("[ok] saved graph: start → 案例 → 读图? → 抽条件 → JSON → 出图 → 修补?")
        print(
            f"[ok] published v{published.get('version')} "
            f"enabled={item.get('enabled')} — 刷新「AI 智能问答」后选择「{WORKFLOW_NAME}」"
        )
        await _retire_legacy_layout_workflow(db, keep_public_id=public_id)


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
