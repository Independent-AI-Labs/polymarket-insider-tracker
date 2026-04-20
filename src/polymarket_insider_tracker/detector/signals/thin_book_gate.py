"""Signal 03-D — Thin-book gate (gate-only, no section).

Spec: docs/signals/03-volume-liquidity.md § 03-D.

A precondition helper, not a surfaced signal. Given market
metadata, returns True when the market's 24h volume is at least
GATE_MIN_RATIO times its book liquidity — the state that makes
price-impact signals (02-A, 02-D, 03-B) meaningful. Other signals
query this via `is_thin_book_ok()` before emitting hits.

Not registered in the newsletter signal registry; imported
directly by signals that need it.
"""

from __future__ import annotations

from typing import Any

GATE_MIN_RATIO = 2.0


def is_thin_book_ok(market_meta: dict[str, Any]) -> bool:
    """Return True when the market has enough volume-to-liquidity
    ratio for price-impact interpretations to hold.
    """
    vol = float(market_meta.get("volume24hr", 0) or 0)
    liq = float(market_meta.get("liquidityClob", 0) or 0)
    if liq <= 0:
        return False
    return (vol / liq) >= GATE_MIN_RATIO
