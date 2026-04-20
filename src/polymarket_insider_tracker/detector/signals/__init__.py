"""Pluggable signal framework. See docs/SPEC-MARKET-SIGNALS.md."""

from .base import (
    ColumnSpec,
    DailyReport,
    SectionSpec,
    Signal,
    SignalContext,
    SignalHit,
)
from .registry import CATEGORY_ORDER, REGISTRY

__all__ = [
    "CATEGORY_ORDER",
    "ColumnSpec",
    "DailyReport",
    "REGISTRY",
    "SectionSpec",
    "Signal",
    "SignalContext",
    "SignalHit",
]
