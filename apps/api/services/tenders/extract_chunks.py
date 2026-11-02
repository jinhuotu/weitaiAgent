"""Compatibility imports; all extraction uses the optimized implementation."""
from api.services.tenders.extract_chunks_optimized import (
    Chunk, split_invitation, merge_payloads, extract_chunks, _CACHE,
)
