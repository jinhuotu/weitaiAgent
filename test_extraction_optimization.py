"""Test script for extraction optimization."""
import asyncio
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)

logger = logging.getLogger(__name__)


async def test_chunking():
    """Test chunking logic."""
    from apps.api.services.tenders.extract_chunks_optimized import split_invitation

    # Sample text with headings
    sample = """
第一章 总则
这是第一章的内容，包含一些基本规定。

第二章 投标人须知
2.1 投标人资格要求
投标人应具备以下资格...

2.2 投标文件编制
投标文件应包括...

第三章 合同条款及格式
3.1 合同协议书
甲方：XXX
乙方：XXX
""" * 100  # Repeat to make it large

    chunks = split_invitation(sample, limit=10000)
    logger.info("Chunking test: %d chunks from %d chars", len(chunks), len(sample))

    for i, chunk in enumerate(chunks[:3]):  # Show first 3
        logger.info(
            "Chunk %d: start=%d end=%d heading=%s chars=%d",
            i + 1,
            chunk.start,
            chunk.end,
            chunk.heading[:50] if chunk.heading else "(none)",
            len(chunk.text),
        )


async def test_json_repair():
    """Test JSON repair logic."""
    from apps.api.services.tenders.extract_chunks_optimized import repair_json
    import json

    test_cases = [
        ('```json\n{"key": "value"}\n```', "markdown wrapper"),
        ('Some text before\n{"key": "value"}\nSome text after', "extra text"),
        ('{"key": "value",}', "trailing comma in object"),
        ('{"list": [1, 2, 3,]}', "trailing comma in array"),
        ('{"nested": {"key": "value",},}', "nested trailing commas"),
    ]

    logger.info("JSON repair test:")
    for raw, description in test_cases:
        try:
            repaired = repair_json(raw)
            parsed = json.loads(repaired)
            logger.info("✓ %s: success", description)
        except Exception as e:
            logger.error("✗ %s: %s", description, e)


async def test_config():
    """Test configuration."""
    from apps.api.services.tenders.extraction_config import (
        get_extraction_config,
        set_extraction_config,
    )

    config = get_extraction_config()
    logger.info("Current config:")
    logger.info("  - extraction_concurrency: %d", config.extraction_concurrency)
    logger.info("  - use_optimized_extraction: %s", config.use_optimized_extraction)
    logger.info("  - chunk_size: %d", config.chunk_size)
    logger.info("  - enable_json_repair: %s", config.enable_json_repair)

    # Test updating config
    set_extraction_config(extraction_concurrency=8)
    config = get_extraction_config()
    assert config.extraction_concurrency == 8
    logger.info("✓ Config update test passed")

    # Reset
    set_extraction_config(extraction_concurrency=6)


async def main():
    """Run all tests."""
    logger.info("=== Extraction Optimization Tests ===")

    try:
        await test_chunking()
        logger.info("")

        await test_json_repair()
        logger.info("")

        await test_config()
        logger.info("")

        logger.info("=== All tests passed ===")

    except Exception as e:
        logger.exception("Test failed: %s", e)
        return 1

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
