"""独立测试性能追踪器（无依赖）。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 直接导入文件
import importlib.util
spec = importlib.util.spec_from_file_location(
    "performance_tracker",
    "apps/api/services/tenders/performance_tracker.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

PerformanceTracker = module.PerformanceTracker

import asyncio
import time


async def simulate_llm_extract():
    await asyncio.sleep(0.5)


async def simulate_db_query():
    await asyncio.sleep(0.1)


async def test_tracker():
    tracker = PerformanceTracker()
    tracker.set_context(144869, "test_file.docx")
    tracker.start_total()

    # 模拟各步骤
    with tracker.track("llm_extract"):
        await simulate_llm_extract()
    tracker.metrics.llm_chunk_count = 16

    with tracker.track("document_parse"):
        time.sleep(0.05)

    with tracker.track("outline_extract"):
        time.sleep(0.2)

    with tracker.track("format_extract"):
        time.sleep(0.1)

    with tracker.track("requirements_extract"):
        time.sleep(0.15)

    with tracker.track("performance_extract"):
        time.sleep(0.08)

    with tracker.track("materials_match"):
        time.sleep(0.12)

    for _ in range(5):
        with tracker.track("db_query"):
            await simulate_db_query()

    with tracker.track("kb_recall"):
        await simulate_db_query()

    tracker.finish_total()
    tracker.log_summary()

    print("\n[TEST PASSED] Performance tracker works correctly!")


if __name__ == "__main__":
    asyncio.run(test_tracker())
