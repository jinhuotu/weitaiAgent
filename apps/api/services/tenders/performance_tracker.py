"""性能计时器，用于追踪 parse_invitation 各步骤耗时。"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from time import perf_counter
from typing import Any

logger = logging.getLogger(__name__)


class TimingMetrics:
    """各步骤计时指标。"""
    def __init__(self):
        # LLM 阶段
        self.llm_extract_seconds: float = 0.0
        self.llm_chunk_count: int = 0

        # 预解析阶段
        self.document_parse_seconds: float = 0.0

        # 本地规则处理阶段（优化目标）
        self.outline_extract_seconds: float = 0.0
        self.format_extract_seconds: float = 0.0
        self.requirements_extract_seconds: float = 0.0
        self.performance_extract_seconds: float = 0.0
        self.materials_match_seconds: float = 0.0
        self.kb_recall_seconds: float = 0.0

        # 数据库查询阶段
        self.db_queries_seconds: float = 0.0
        self.db_query_count: int = 0

        # 其他阶段
        self.other_processing_seconds: float = 0.0

        # 总计
        self.total_seconds: float = 0.0

        # 上下文
        self.char_count: int = 0
        self.file_name: str = ""

    def log_summary(self):
        """输出详细的性能报告。"""
        if self.total_seconds <= 0:
            logger.warning("timing_metrics: no data collected")
            return

        # 计算各阶段占比
        llm_pct = (self.llm_extract_seconds / self.total_seconds * 100) if self.total_seconds > 0 else 0
        parse_pct = (self.document_parse_seconds / self.total_seconds * 100) if self.total_seconds > 0 else 0
        local_total = (
            self.outline_extract_seconds +
            self.format_extract_seconds +
            self.requirements_extract_seconds +
            self.performance_extract_seconds +
            self.materials_match_seconds
        )
        local_pct = (local_total / self.total_seconds * 100) if self.total_seconds > 0 else 0
        db_pct = (self.db_queries_seconds / self.total_seconds * 100) if self.total_seconds > 0 else 0
        kb_pct = (self.kb_recall_seconds / self.total_seconds * 100) if self.total_seconds > 0 else 0
        other_pct = (self.other_processing_seconds / self.total_seconds * 100) if self.total_seconds > 0 else 0

        logger.info("=" * 80)
        logger.info("PARSE INVITATION PERFORMANCE SUMMARY")
        logger.info("=" * 80)
        logger.info("File: %s (%d chars)", self.file_name or "unknown", self.char_count)
        logger.info("")
        logger.info("TOTAL TIME: %.2f seconds", self.total_seconds)
        logger.info("")

        logger.info("BREAKDOWN:")
        logger.info("  1. LLM Extract:        %7.2fs (%5.1f%%) - %d chunks",
                    self.llm_extract_seconds, llm_pct, self.llm_chunk_count)
        logger.info("  2. Document Parse:     %7.2fs (%5.1f%%) - one-pass structure",
                    self.document_parse_seconds, parse_pct)
        logger.info("  3. Local Rules:        %7.2fs (%5.1f%%) - regex & extraction",
                    local_total, local_pct)
        logger.info("       - Outline:        %7.2fs", self.outline_extract_seconds)
        logger.info("       - Format:         %7.2fs", self.format_extract_seconds)
        logger.info("       - Requirements:   %7.2fs", self.requirements_extract_seconds)
        logger.info("       - Performance:    %7.2fs", self.performance_extract_seconds)
        logger.info("       - Materials:      %7.2fs", self.materials_match_seconds)
        logger.info("  4. DB Queries:         %7.2fs (%5.1f%%) - %d queries",
                    self.db_queries_seconds, db_pct, self.db_query_count)
        logger.info("  5. KB Recall:          %7.2fs (%5.1f%%)", self.kb_recall_seconds, kb_pct)
        logger.info("  6. Other:              %7.2fs (%5.1f%%)", self.other_processing_seconds, other_pct)
        logger.info("=" * 80)

        # 性能评估
        chars_per_sec = self.char_count / self.total_seconds if self.total_seconds > 0 else 0
        logger.info("PERFORMANCE: %.0f chars/second", chars_per_sec)

        if local_total > self.llm_extract_seconds:
            logger.warning("⚠️  Local rules (%.2fs) > LLM extract (%.2fs) - optimization needed!",
                          local_total, self.llm_extract_seconds)
        else:
            logger.info("✓ Local rules (%.2fs) < LLM extract (%.2fs) - balanced",
                       local_total, self.llm_extract_seconds)

        logger.info("=" * 80)


class PerformanceTracker:
    """性能追踪器，用于计时各步骤。"""

    def __init__(self):
        self.metrics = TimingMetrics()
        self._start_time: float = 0.0
        self._step_start: float = 0.0

    def start_total(self):
        """开始总计时。"""
        self._start_time = perf_counter()

    def finish_total(self):
        """结束总计时。"""
        self.metrics.total_seconds = perf_counter() - self._start_time

    @contextmanager
    def track(self, step: str):
        """追踪某个步骤的耗时。

        Usage:
            with tracker.track("llm_extract"):
                await extract_chunks(...)
        """
        start = perf_counter()
        try:
            yield
        finally:
            elapsed = perf_counter() - start

            # 记录到对应字段
            if step == "llm_extract":
                self.metrics.llm_extract_seconds += elapsed
            elif step == "document_parse":
                self.metrics.document_parse_seconds += elapsed
            elif step == "outline_extract":
                self.metrics.outline_extract_seconds += elapsed
            elif step == "format_extract":
                self.metrics.format_extract_seconds += elapsed
            elif step == "requirements_extract":
                self.metrics.requirements_extract_seconds += elapsed
            elif step == "performance_extract":
                self.metrics.performance_extract_seconds += elapsed
            elif step == "materials_match":
                self.metrics.materials_match_seconds += elapsed
            elif step == "kb_recall":
                self.metrics.kb_recall_seconds += elapsed
            elif step == "db_query":
                self.metrics.db_queries_seconds += elapsed
                self.metrics.db_query_count += 1
            else:
                self.metrics.other_processing_seconds += elapsed

            logger.info("step=%s elapsed=%.3fs", step, elapsed)

    def set_context(self, char_count: int, file_name: str = ""):
        """设置上下文信息。"""
        self.metrics.char_count = char_count
        self.metrics.file_name = file_name

    def log_summary(self):
        """输出性能报告。"""
        self.metrics.log_summary()
