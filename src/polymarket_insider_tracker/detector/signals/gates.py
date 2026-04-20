"""Shared market-eligibility gates used by every signal.

Centralises the pre-filters M.'s review flagged as missing:

- **Price-extreme gate.** Exclude markets at the extremes of
  implied probability (default [0.05, 0.95]). Fed-at-0.001
  markets produce phantom OFI because small capital through thin
  illiquid prices looks like 100 % imbalance — it isn't.
- **Resolution-window gate.** Exclude markets closing within N
  hours. Drift-to-resolution creates mechanical one-sidedness.
- **Life-total gate.** Exclude markets whose total lifespan is
  < N hours. Catches 5-minute crypto candle bots AND sports
  game-day markets where co-timing is inevitable, not informational.
- **Novelty-skip gate.** Hand-curated blocklist of markets whose
  resolution is not probabilistic (Schelling-point jokes,
  eschatological markets, etc.).
- **Liquidity / thin-book gate.** For price-impact interpretations
  — spec § 03-D.

Signals compose these at call time. Thresholds default to the
conservative values in `DEFAULT_GATES`; signals can override via
their `__init__` so threshold tuning happens per-signal, not
globally.

Every gate returns True when the market PASSES (is eligible).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


# Hand-curated skip-list. These markets resolve on criteria that
# aren't probabilistic in the informed-trading sense — flow on them
# is either jokes, meme-trading, or always-will-resolve-NO bets.
# Add a slug prefix or exact conditionId here to suppress.
NOVELTY_SLUG_PREFIXES: tuple[str, ...] = (
    "will-jesus-christ-return",
    "will-aliens",
    "will-god",
    "will-satan",
    "will-the-rapture",
)

# Category-level skips. Gamma-api's `category` field.
NOVELTY_CATEGORIES: frozenset[str] = frozenset({
    "joke", "novelty",
})


@dataclass(frozen=True)
class GateConfig:
    """Per-signal gate thresholds. Everything tunable in one place."""

    # Price-extreme band (inclusive).
    min_price: float = 0.05
    max_price: float = 0.95

    # Hours until resolution a market must have to be eligible.
    # Informed flow into a market that resolves in 4 hours is not
    # distinguishable from mechanical resolution drift.
    min_hours_to_close: float = 24.0

    # Total lifespan of the market — excludes 5-minute crypto
    # candles AND sports game-day markets.
    min_life_hours: float = 24.0

    # Volume-to-liquidity ratio required for price-impact signals
    # (thin-book gate, spec § 03-D).
    min_thin_book_ratio: float = 2.0

    # Enforce the novelty blocklist.
    apply_novelty_skip: bool = True


DEFAULT_GATES = GateConfig()


# ── Individual gate checks ───────────────────────────────────────────


def price_in_band(market_meta: dict[str, Any], cfg: GateConfig) -> bool:
    """Last price inside the non-extreme band."""
    raw = market_meta.get("lastTradePrice")
    if raw is None:
        # Fall back to bestBid / bestAsk midpoint.
        bid = market_meta.get("bestBid")
        ask = market_meta.get("bestAsk")
        if bid is None and ask is None:
            return False
        bid = float(bid or 0)
        ask = float(ask or 1)
        raw = (bid + ask) / 2 if (bid or ask) else None
        if raw is None:
            return False
    try:
        price = float(raw)
    except (TypeError, ValueError):
        return False
    return cfg.min_price <= price <= cfg.max_price


def has_enough_time_to_close(market_meta: dict[str, Any], cfg: GateConfig) -> bool:
    """Market's endDate is more than `min_hours_to_close` in the future."""
    end_raw = str(market_meta.get("endDate", "") or market_meta.get("endDateIso", ""))
    if not end_raw:
        # Unknown end-date — treat as passing; be permissive when we
        # don't have the data rather than silently suppressing.
        return True
    try:
        end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
    except ValueError:
        return True
    now = datetime.now(timezone.utc)
    return (end_dt - now) > timedelta(hours=cfg.min_hours_to_close)


def has_enough_lifespan(market_meta: dict[str, Any], cfg: GateConfig) -> bool:
    """Market exists for more than `min_life_hours` total.

    Catches:
      - 5-minute crypto candle bot markets (life ≈ 5 min)
      - Sports game-day markets (life ≈ 24h — by default we're
        exactly at the boundary, which excludes them; tune if
        you want to include)
    """
    start_raw = str(market_meta.get("startDate", "") or market_meta.get("startDateIso", ""))
    end_raw = str(market_meta.get("endDate", "") or market_meta.get("endDateIso", ""))
    if not start_raw or not end_raw:
        return True  # be permissive when missing
    try:
        start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
    except ValueError:
        return True
    return (end_dt - start_dt) >= timedelta(hours=cfg.min_life_hours)


def is_not_novelty(market_meta: dict[str, Any], cfg: GateConfig) -> bool:
    """Market doesn't match the curated novelty blocklist."""
    if not cfg.apply_novelty_skip:
        return True
    slug = str(market_meta.get("slug", "") or "").lower()
    for prefix in NOVELTY_SLUG_PREFIXES:
        if slug.startswith(prefix):
            return False
    category = str(market_meta.get("category", "") or "").lower()
    return category not in NOVELTY_CATEGORIES


def thin_book_ratio_ok(market_meta: dict[str, Any], cfg: GateConfig) -> bool:
    """Volume/liquidity ratio passes for price-impact signals."""
    vol = float(market_meta.get("volume24hr", 0) or 0)
    liq = float(market_meta.get("liquidityClob", 0) or 0)
    if liq <= 0:
        return False
    return (vol / liq) >= cfg.min_thin_book_ratio


# ── Combined gate ────────────────────────────────────────────────────


def passes_all(
    market_meta: dict[str, Any],
    cfg: GateConfig,
    *,
    require_price: bool = True,
    require_time: bool = True,
    require_lifespan: bool = True,
    require_novelty_skip: bool = True,
    require_liquidity: bool = False,
) -> bool:
    """Apply a selected subset of gates.

    Signals pick which gates apply to them (e.g. fresh-wallet
    doesn't care about liquidity; price-impact-style signals do).
    """
    if require_price and not price_in_band(market_meta, cfg):
        return False
    if require_time and not has_enough_time_to_close(market_meta, cfg):
        return False
    if require_lifespan and not has_enough_lifespan(market_meta, cfg):
        return False
    if require_novelty_skip and not is_not_novelty(market_meta, cfg):
        return False
    if require_liquidity and not thin_book_ratio_ok(market_meta, cfg):
        return False
    return True
