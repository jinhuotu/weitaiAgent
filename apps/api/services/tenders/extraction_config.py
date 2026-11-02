"""Configuration for tender extraction optimization.

Adjust these parameters to tune extraction performance:
- EXTRACTION_CONCURRENCY: Higher = faster but may hit rate limits
- USE_OPTIMIZED_EXTRACTION: Enable/disable new optimized pipeline
- CHUNK_SIZE: Size of each text chunk (larger = fewer chunks but higher token usage)
"""

from dataclasses import dataclass


@dataclass
class ExtractionConfig:
    """Configuration for extraction pipeline."""

    # Concurrency settings
    extraction_concurrency: int = 6
    """Number of concurrent extraction requests (was 3, now 6)."""

    enable_backoff: bool = True
    """Enable exponential backoff on rate limit errors."""

    # Context optimization
    use_optimized_extraction: bool = True
    """Use optimized extraction with minimal context duplication."""

    lightweight_catalog: bool = True
    """Use condensed catalog in per-chunk prompts (key:title only)."""

    # Chunk configuration
    chunk_size: int = 10000
    """Maximum characters per chunk (default 10000)."""

    subdivision_size: int = 4000
    """Size when subdividing failed chunks (was len//2, now fixed at 4000)."""

    # JSON parsing
    enable_json_repair: bool = True
    """Attempt to repair malformed JSON before failing."""

    # Logging and metrics
    log_chunk_metrics: bool = True
    """Log detailed metrics for each chunk."""

    log_final_summary: bool = True
    """Log extraction summary with success rates."""

    # Feature flags
    enable_json_mode: bool = True
    """Request JSON mode from LLM if supported."""

    skip_heavy_context: bool = True
    """Skip playbooks and KB hits in per-chunk prompts (inject in system only)."""


# Global instance
_config = ExtractionConfig()


def get_extraction_config() -> ExtractionConfig:
    """Get the global extraction configuration."""
    return _config


def set_extraction_config(**kwargs):
    """Update global extraction configuration.

    Example:
        set_extraction_config(extraction_concurrency=8, chunk_size=12000)
    """
    global _config
    for key, value in kwargs.items():
        if hasattr(_config, key):
            setattr(_config, key, value)
        else:
            raise ValueError(f"Unknown config key: {key}")
