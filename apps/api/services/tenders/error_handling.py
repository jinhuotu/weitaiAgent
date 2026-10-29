"""Enhanced error handling and logging for tender parsing.

Add detailed logging at each step to help diagnose parsing failures.
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Any, Callable, TypeVar

from common.errors import AppError, ErrorCode

logger = logging.getLogger("api.tenders.extract")

T = TypeVar("T")


def log_parsing_step(step_name: str):
    """Decorator to log execution time and errors for each parsing step."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> T:
            start = time.time()
            logger.info(f"[{step_name}] Starting...")
            try:
                result = await func(*args, **kwargs)
                elapsed = time.time() - start
                logger.info(f"[{step_name}] Completed in {elapsed:.2f}s")
                return result
            except Exception as e:
                elapsed = time.time() - start
                logger.error(
                    f"[{step_name}] Failed after {elapsed:.2f}s: {type(e).__name__}: {str(e)}",
                    exc_info=True,
                )
                raise

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> T:
            start = time.time()
            logger.info(f"[{step_name}] Starting...")
            try:
                result = func(*args, **kwargs)
                elapsed = time.time() - start
                logger.info(f"[{step_name}] Completed in {elapsed:.2f}s")
                return result
            except Exception as e:
                elapsed = time.time() - start
                logger.error(
                    f"[{step_name}] Failed after {elapsed:.2f}s: {type(e).__name__}: {str(e)}",
                    exc_info=True,
                )
                raise

        # Return appropriate wrapper based on whether function is async
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore[return-value]
        return sync_wrapper  # type: ignore[return-value]

    return decorator


def handle_parsing_error(error: Exception, context: str = "") -> AppError:
    """Convert common parsing errors to user-friendly AppError with context."""
    error_str = str(error).lower()
    error_type = type(error).__name__

    # Database connection errors
    if "connection" in error_str or "connect" in error_str:
        if "mysql" in error_str or "database" in error_str:
            return AppError(
                ErrorCode.INTERNAL,
                f"数据库连接失败{context}。请检查 MySQL 服务是否正常运行。",
                status_code=500,
            )
        if "redis" in error_str:
            return AppError(
                ErrorCode.INTERNAL,
                f"Redis 连接失败{context}。请检查 Redis 服务是否正常运行。",
                status_code=500,
            )
        if "qdrant" in error_str:
            return AppError(
                ErrorCode.INTERNAL,
                f"向量数据库连接失败{context}。请检查 Qdrant 服务是否正常运行。",
                status_code=500,
            )

    # Timeout errors
    if "timeout" in error_str:
        if "ocr" in error_str:
            return AppError(
                ErrorCode.INTERNAL,
                f"OCR 识别超时{context}。建议：1) 使用文字版 Word 而非扫描 PDF；2) 提高 OCR_TIMEOUT_SECONDS 配置。",
                status_code=500,
            )
        if "llm" in error_str or "model" in error_str:
            return AppError(
                ErrorCode.INTERNAL,
                f"模型调用超时{context}。请检查 LLM 服务是否正常，或稍后重试。",
                status_code=500,
            )
        return AppError(
            ErrorCode.INTERNAL,
            f"处理超时{context}。文件可能过大，请尝试使用较小的文件或联系管理员。",
            status_code=500,
        )

    # Authentication errors
    if any(keyword in error_str for keyword in ["unauthorized", "authentication", "invalid key", "invalid token"]):
        return AppError(
            ErrorCode.UNAUTHORIZED,
            f"鉴权失败{context}。请检查「模型管理」中的 API Key 配置，确认密钥有效且账户余额充足。",
            status_code=401,
        )

    # Rate limit errors
    if "rate limit" in error_str or "too many requests" in error_str:
        return AppError(
            ErrorCode.INTERNAL,
            f"请求过于频繁{context}。请稍后再试。",
            status_code=429,
        )

    # File processing errors
    if "file" in error_str or "upload" in error_str:
        return AppError(
            ErrorCode.VALIDATION,
            f"文件处理失败{context}。请确认文件格式正确且未损坏。支持的格式：Word (.doc/.docx)、PDF。",
            status_code=422,
        )

    # OCR errors
    if "ocr" in error_str:
        return AppError(
            ErrorCode.INTERNAL,
            f"OCR 识别失败{context}。请检查 OCR 服务配置，或使用文字版 Word 文档。",
            status_code=500,
        )

    # JSON parsing errors
    if error_type in ("JSONDecodeError", "ValidationError") or "json" in error_str:
        return AppError(
            ErrorCode.VALIDATION,
            f"数据解析失败{context}。DeepSeek-R1 等推理模型可能输出不完整，建议改用 DeepSeek-V3 或 Qwen 等对话模型。",
            status_code=422,
        )

    # Memory errors
    if "memory" in error_str or error_type == "MemoryError":
        return AppError(
            ErrorCode.INTERNAL,
            f"内存不足{context}。文件可能过大，请尝试使用较小的文件。",
            status_code=500,
        )

    # Buffer errors (asyncmy on Windows)
    if error_type == "BufferError":
        return AppError(
            ErrorCode.INTERNAL,
            f"缓冲区错误{context}。请重启 API 服务，或尝试升级 asyncmy 到 0.3.x 版本。",
            status_code=500,
        )

    # Generic error with original message
    return AppError(
        ErrorCode.INTERNAL,
        f"处理失败{context}：{str(error)}。请查看日志获取详细信息或联系管理员。",
        status_code=500,
    )


def log_parse_context(
    filename: str = "",
    file_size: int = 0,
    kb_ids: list[str] | None = None,
    user_id: int | None = None,
) -> None:
    """Log context information at the start of parsing."""
    logger.info(
        "Starting tender parsing: filename=%s, size=%d bytes, kb_ids=%s, user_id=%s",
        filename,
        file_size,
        kb_ids or [],
        user_id,
    )
