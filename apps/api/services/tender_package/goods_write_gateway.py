"""Stable write facade for structured cable imports.

This is the only cross-module write surface; validation and provenance remain in
goods_inputs.confirm_offer/confirm_price.
"""
from .goods_inputs import confirm_offer, confirm_price

__all__ = ["confirm_offer", "confirm_price"]
