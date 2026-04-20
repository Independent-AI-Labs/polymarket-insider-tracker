"""Signal 01-A — Fresh wallet (Polymarket-first-trade variant).

Spec: docs/signals/01-informed-flow.md § 01-A.

Without Tier-3 Polygon RPC we cannot read the wallet's on-chain
`first_seen` timestamp. The spec describes a fallback that uses
the wallet's Polymarket trade history instead: a wallet whose
first observable trade on Polymarket is within N days of the
flagged trade is "fresh" for signal purposes. That's what this
module implements — real data, no synthetic fallback.

Each hit carries: wallet, market, side, notional, days-on-
Polymarket (as rendered in the section's "First seen" column).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from .base import (
    ColumnSpec,
    Signal,
    SignalContext,
    SignalHit,
    _money,
    _short_wallet,
)
from .gates import DEFAULT_GATES, GateConfig, passes_all

LOG = logging.getLogger(__name__)

DATA_API = "https://data-api.polymarket.com"
HISTORY_LIMIT = 1000
# Don't re-probe the same wallet in the same run.
_wallet_cache: dict[str, datetime | None] = {}


class FreshWalletSignal(Signal):
    id = "01-A-fresh-wallet"
    name = "Fresh wallets"
    category = "informed_flow"
    reliability_band = "medium"
    hide_when_empty = True

    def __init__(
        self,
        *,
        max_days: float = 30.0,
        min_trade_notional: float = 10_000.0,
        top_n: int = 5,
        gate_config: GateConfig | None = None,
    ) -> None:
        self.max_days = max_days
        self.min_trade_notional = min_trade_notional
        self.top_n = top_n
        self.gates = gate_config or DEFAULT_GATES
        self.description = (
            f"Wallets whose earliest observable Polymarket trade is "
            f"within {self.max_days:.0f} days of today AND whose "
            f"flagged trade is ≥ {_money(self.min_trade_notional)} — "
            "the behavioural fingerprint of a just-funded account "
            "opening a size-meaningful position."
        )

    def columns(self) -> list[ColumnSpec]:
        return [
            ColumnSpec("wallet_display", "Wallet", "left", "wallet",
                       link_field="wallet_url"),
            ColumnSpec("market_title", "Market", "left", "text",
                       link_field="market_url", width_hint="42%"),
            ColumnSpec("side", "Side", "left", "text"),
            ColumnSpec("notional_fmt", "Notional", "right", "money"),
            ColumnSpec("first_seen_fmt", "First seen", "right", "duration"),
        ]

    def compute(self, context: SignalContext) -> list[SignalHit]:
        if not context.trades:
            return []

        # Largest trade per wallet in the window — but gated on
        # (a) per-trade notional ≥ min_trade_notional
        # (b) market passes the price / lifespan / novelty gates.
        by_wallet: dict[str, dict[str, Any]] = {}
        for t in context.trades:
            wallet = str(t.get("proxyWallet", "")).lower()
            if not wallet:
                continue
            notional = Decimal(str(t.get("size", 0))) * Decimal(
                str(t.get("price", 0))
            )
            if float(notional) < self.min_trade_notional:
                continue
            mid = str(t.get("conditionId", "")).lower()
            meta = context.market_meta.get(mid) or {}
            # Fresh-wallet signal doesn't require the thin-book gate
            # (it's wallet-level, not a price-impact claim) — but it
            # DOES require price-band, lifespan and novelty gates.
            if not passes_all(
                meta,
                self.gates,
                require_price=True,
                require_time=True,
                require_lifespan=True,
                require_novelty_skip=True,
                require_liquidity=False,
            ):
                continue
            existing = by_wallet.get(wallet)
            if existing is None or notional > existing["_notional"]:
                by_wallet[wallet] = {**t, "_notional": notional}

        if not by_wallet:
            return []

        # Probe Polymarket first-trade for each candidate.
        candidates = list(by_wallet.keys())
        first_trades = asyncio.run(
            _fetch_first_trade_timestamps(candidates)
        )

        now = context.window_end or datetime.now(UTC)
        cutoff = now - timedelta(days=self.max_days)

        hits: list[SignalHit] = []
        for wallet, trade in by_wallet.items():
            first_trade_ts = first_trades.get(wallet)
            if first_trade_ts is None:
                continue
            if first_trade_ts < cutoff:
                continue
            days = (now - first_trade_ts).total_seconds() / 86400
            notional = float(trade["_notional"])
            hits.append(
                SignalHit(
                    signal_id=self.id,
                    wallet_address=wallet,
                    market_id=str(trade.get("conditionId", "")),
                    market_title=str(trade.get("title", "")),
                    event_slug=str(trade.get("eventSlug", "")),
                    score=min(1.0, 0.5 + 0.5 * (1 - days / self.max_days)),
                    row={
                        "wallet_address": wallet,
                        "wallet_display": _short_wallet(wallet),
                        "wallet_url": f"https://polymarket.com/profile/{wallet}",
                        "market_id": str(trade.get("conditionId", "")),
                        "market_title": str(trade.get("title", "")),
                        "market_url": f"https://polymarket.com/event/{trade.get('eventSlug', '')}",
                        "side": str(trade.get("side", "")),
                        "notional": notional,
                        "notional_fmt": _money(notional),
                        "first_seen_ts": first_trade_ts.isoformat(),
                        "first_seen_fmt": _fmt_first_seen(days),
                    },
                    headline_fragment="",
                )
            )

        hits.sort(key=lambda h: (h.score, h.row["notional"]), reverse=True)
        hits = hits[:self.top_n]

        # The top hit carries the section's headline fragment.
        if hits:
            top = hits[0]
            n_fresh = len(hits)
            fragment = (
                f"{n_fresh} fresh "
                f"wallet{'s' if n_fresh != 1 else ''} opened "
                f"positions — heaviest: {_money(top.row['notional'])} on "
                f"<em>{top.market_title}</em> from a "
                f"{top.row['first_seen_fmt']}-old wallet"
            )
            hits[0] = SignalHit(**{**hits[0].__dict__, "headline_fragment": fragment})

        return hits


def _fmt_first_seen(days: float) -> str:
    if days < 1:
        hours = int(days * 24)
        return f"{max(hours, 1)}h"
    return f"{int(days)}d"


async def _fetch_first_trade_timestamps(
    wallets: list[str],
) -> dict[str, datetime | None]:
    """For each wallet, find its earliest Polymarket trade timestamp.

    We probe data-api once per wallet (paginated) — not cheap, but
    cached in `_wallet_cache` across a single newsletter-daily run.
    """
    todo = [w for w in wallets if w not in _wallet_cache]
    if not todo:
        return {w: _wallet_cache.get(w) for w in wallets}

    async with httpx.AsyncClient(timeout=30.0) as client:
        sem = asyncio.Semaphore(8)  # 8 parallel probes; polite-rate

        async def probe(wallet: str) -> tuple[str, datetime | None]:
            async with sem:
                # Newest-first — the oldest trade is either the
                # last row of the 1000-row page or we saw fewer than
                # 1000 rows and the oldest is the last row.
                try:
                    r = await client.get(
                        f"{DATA_API}/trades",
                        params={"user": wallet, "limit": HISTORY_LIMIT},
                    )
                    r.raise_for_status()
                    rows = r.json()
                except (httpx.HTTPError, ValueError) as exc:
                    LOG.warning("first-trade probe failed for %s: %s", wallet, exc)
                    return wallet, None
                if not isinstance(rows, list) or not rows:
                    # No trade history returned — wallet has only
                    # traded in the current window (which IS fresh);
                    # fall back to "oldest trade in context window".
                    return wallet, None
                oldest_ts = min(int(r.get("timestamp", 0)) for r in rows)
                return wallet, datetime.fromtimestamp(oldest_ts, UTC)

        results = await asyncio.gather(*[probe(w) for w in todo])

    for wallet, ts in results:
        _wallet_cache[wallet] = ts
    return {w: _wallet_cache.get(w) for w in wallets}
