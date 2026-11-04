"""测试性能追踪器。"""
import asyncio
import time
from apps.api.services.tenders.performance_tracker import PerformanceTracker


async def simulate_llm_extract():
    """模拟 LLM 抽取。"""
    await asyncio.sleep(0.5)


async def simulate_db_query():
    """模拟数据库查询。"""
    await asyncio.sleep(0.1)


async def simulate_local_rules():
    """模拟本地规则处理。"""
    time.sleep(0.2)  # 同步操作


async def test_tracker():
    """测试性能追踪。"""
    tracker = PerformanceTracker()
    tracker.set_context(144869, "test_file.docx")
    tracker.start_total()

    # 模拟 LLM 抽取
    with tracker.track("llm_extract"):
        await simulate_llm_extract()
    tracker.metrics.llm_chunk_count = 16

    # 模拟文档解析
    with tracker.track("document_parse"):
        time.sleep(0.05)

    # 模拟本地规则
    with tracker.track("outline_extract"):
        simulate_local_rules()

    with tracker.track("format_extract"):
        time.sleep(0.1)

    with tracker.track("requirements_extract"):
        time.sleep(0.15)

    with tracker.track("performance_extract"):
        time.sleep(0.08)

    with tracker.track("materials_match"):
        time.sleep(0.12)

    # 模拟数据库查询
    for _ in range(5):
        with tracker.track("db_query"):
            await simulate_db_query()

    # 模拟知识库召回
    with tracker.track("kb_recall"):
        await simulate_db_query()

    # 结束计时
    tracker.finish_total()
    tracker.log_summary()


if __name__ == "__main__":
    asyncio.run(test_tracker())
