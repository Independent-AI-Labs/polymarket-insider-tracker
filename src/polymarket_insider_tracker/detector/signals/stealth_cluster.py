"""Signal 02-C — Stealth cluster (multi-wallet variant).

Spec: docs/signals/02-microstructure.md § 02-C.

The 01-B Variant B "one wallet stealthy on one market" case lives
inside unusual_size.py. This signal flags a stronger pattern: ≥ 3
DIFFERENT wallets each producing mid-size trades on the same
market within an hour. That's co-timed informed flow, not one
account slicing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from .base import (
    ColumnSpec,
    Signal,
    SignalContext,
    SignalHit,
    _money,
)

CLUSTER_WINDOW_SECONDS = 3600  # 1 hour
CLUSTER_MIN_WALLETS = 3
CLUSTER_MIN_TOTAL_NOTIONAL = 50_000  # USDC combined


class StealthClusterSignal(Signal):
    id = "02-C-stealth-cluster"
    name = "Co-timed wallet clusters"
    category = "microstructure"
    description = (
        f"≥ {CLUSTER_MIN_WALLETS} distinct wallets each opening "
        f"positions on the same market within a rolling "
        f"{CLUSTER_WINDOW_SECONDS // 60}-minute window, summing to "
        f"≥ {_money(CLUSTER_MIN_TOTAL_NOTIONAL)} combined notional. "
        "Often coordination, sometimes simultaneous reaction to a "
        "private-news flash."
    )
    reliability_band = "medium"
    hide_when_empty = True

    def columns(self) -> list[ColumnSpec]:
        return [
            ColumnSpec("market_title", "Market", "left", "text",
                       link_field="market_url", width_hint="40%"),
            ColumnSpec("window_start_fmt", "Window start", "left", "datetime"),
            ColumnSpec("wallet_count", "Wallets", "right", "int"),
            ColumnSpec("span_s_fmt", "Span", "right", "duration"),
            ColumnSpec("combined_notional_fmt", "Combined", "right", "money"),
        ]

    def compute(self, context: SignalContext) -> list[SignalHit]:
        if not context.trades:
            return []

        by_market: dict[str, list[dict[str, Any]]] = {}
        for t in context.trades:
            mid = str(t.get("conditionId", ""))
            if mid:
                by_market.setdefault(mid, []).append(t)

        hits: list[SignalHit] = []
        for mid, market_trades in by_market.items():
            market_trades.sort(key=lambda r: int(r.get("timestamp", 0)))
            # Anchor at each trade, extend forward until the window
            # closes; dedupe by bucket.
            seen_buckets: set[int] = set()
            for i, anchor in enumerate(market_trades):
                anchor_ts = int(anchor.get("timestamp", 0))
                bucket = anchor_ts // CLUSTER_WINDOW_SECONDS
                if bucket in seen_buckets:
                    continue
                wallets: set[str] = set()
                notional = Decimal("0")
                last_ts = anchor_ts
                for j in range(i, len(market_trades)):
                    tj = market_trades[j]
                    ts = int(tj.get("timestamp", 0))
                    if ts - anchor_ts > CLUSTER_WINDOW_SECONDS:
                        break
                    wallets.add(str(tj.get("proxyWallet", "")).lower())
                    notional += Decimal(str(tj.get("size", 0))) * Decimal(
                        str(tj.get("price", 0))
                    )
                    last_ts = ts
                if len(wallets) < CLUSTER_MIN_WALLETS:
                    continue
                if notional < CLUSTER_MIN_TOTAL_NOTIONAL:
                    continue
                seen_buckets.add(bucket)
                hits.append(
                    SignalHit(
                        signal_id=self.id,
                        wallet_address="",
                        market_id=mid,
                        market_title=str(anchor.get("title", "")),
                        event_slug=str(anchor.get("eventSlug", "")),
                        score=min(1.0, 0.4 + 0.1 * (len(wallets) - CLUSTER_MIN_WALLETS)
                                      + min(float(notional) / 500_000, 0.5)),
                        row={
                            "market_title": str(anchor.get("title", "")),
                            "market_url": f"https://polymarket.com/event/{anchor.get('eventSlug', '')}",
                            "window_start_ts": anchor_ts,
                            "window_start_fmt": datetime.fromtimestamp(anchor_ts, UTC).strftime("%Y-%m-%d %H:%M UTC"),
                            "wallet_count": len(wallets),
                            "span_s": last_ts - anchor_ts,
                            "span_s_fmt": f"{last_ts - anchor_ts}s",
                            "combined_notional": float(notional),
                            "combined_notional_fmt": _money(notional),
                        },
                    )
                )

        hits.sort(
            key=lambda h: (h.row["wallet_count"], h.row["combined_notional"]),
            reverse=True,
        )
        hits = hits[:5]

        if hits:
            top = hits[0]
            fragment = (
                f"{top.row['wallet_count']} wallets co-timed onto "
                f"<em>{top.market_title}</em> "
                f"({top.row['combined_notional_fmt']} in "
                f"{top.row['span_s_fmt']})"
            )
            hits[0] = SignalHit(**{**hits[0].__dict__, "headline_fragment": fragment})
        return hits
