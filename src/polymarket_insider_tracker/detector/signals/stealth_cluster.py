"""Signal 02-C — Co-timed wallet clusters.

Spec: docs/signals/02-microstructure.md § 02-C.

≥ N distinct wallets opening positions on the same market within
a rolling window. Market-level signal; exposes the participating
wallet list so the reader can drill.
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
    _short_wallet,
    _wallet_list_html,
)
from .gates import DEFAULT_GATES, GateConfig, passes_all


class StealthClusterSignal(Signal):
    id = "02-C-stealth-cluster"
    name = "Co-timed wallet clusters"
    category = "microstructure"
    reliability_band = "medium"
    hide_when_empty = True

    def __init__(
        self,
        *,
        window_seconds: int = 3600,
        min_wallets: int = 3,
        min_total_notional: float = 50_000.0,
        top_contributors: int = 3,
        top_n: int = 5,
        gate_config: GateConfig | None = None,
    ) -> None:
        self.window_seconds = window_seconds
        self.min_wallets = min_wallets
        self.min_total_notional = min_total_notional
        self.top_contributors = top_contributors
        self.top_n = top_n
        self.gates = gate_config or DEFAULT_GATES
        self.description = (
            f"≥ {self.min_wallets} distinct wallets each opening "
            f"positions on the same market within a rolling "
            f"{self.window_seconds // 60}-minute window, combined "
            f"≥ {_money(self.min_total_notional)}. Markets at extreme "
            "prices, closing < 24 h out, or with lifespans < 24 h "
            "(crypto candles, sports game-day) are excluded — those "
            "produce inevitable co-timing that isn't informational."
        )

    def columns(self) -> list[ColumnSpec]:
        return [
            ColumnSpec("market_title", "Market", "left", "text",
                       link_field="market_url", width_hint="32%"),
            ColumnSpec("window_start_fmt", "Window start", "left", "datetime"),
            ColumnSpec("wallet_count", "Wallets", "right", "int"),
            ColumnSpec("span_s_fmt", "Span", "right", "duration"),
            ColumnSpec("combined_notional_fmt", "Combined", "right", "money"),
            ColumnSpec("top_wallets_fmt", "Top contributors", "left", "html"),
        ]

    def compute(self, context: SignalContext) -> list[SignalHit]:
        if not context.trades:
            return []

        eligible: set[str] = set()
        for mid, meta in context.market_meta.items():
            if passes_all(
                meta,
                self.gates,
                require_price=True,
                require_time=True,
                require_lifespan=True,
                require_novelty_skip=True,
                require_liquidity=False,
            ):
                eligible.add(mid.lower())

        by_market: dict[str, list[dict[str, Any]]] = {}
        for t in context.trades:
            mid = str(t.get("conditionId", "")).lower()
            if mid and mid in eligible:
                by_market.setdefault(mid, []).append(t)

        hits: list[SignalHit] = []
        for mid, market_trades in by_market.items():
            market_trades.sort(key=lambda r: int(r.get("timestamp", 0)))
            seen_buckets: set[int] = set()
            for i, anchor in enumerate(market_trades):
                anchor_ts = int(anchor.get("timestamp", 0))
                bucket = anchor_ts // self.window_seconds
                if bucket in seen_buckets:
                    continue
                wallet_notional: dict[str, Decimal] = {}
                notional = Decimal("0")
                last_ts = anchor_ts
                for j in range(i, len(market_trades)):
                    tj = market_trades[j]
                    ts = int(tj.get("timestamp", 0))
                    if ts - anchor_ts > self.window_seconds:
                        break
                    wallet = str(tj.get("proxyWallet", "")).lower()
                    n = Decimal(str(tj.get("size", 0))) * Decimal(
                        str(tj.get("price", 0))
                    )
                    if wallet:
                        wallet_notional[wallet] = (
                            wallet_notional.get(wallet, Decimal("0")) + n
                        )
                    notional += n
                    last_ts = ts
                if len(wallet_notional) < self.min_wallets:
                    continue
                if float(notional) < self.min_total_notional:
                    continue
                seen_buckets.add(bucket)

                top_contribs = sorted(
                    wallet_notional.items(),
                    key=lambda p: p[1],
                    reverse=True,
                )[: self.top_contributors]
                top_wallets_fmt = _wallet_list_html(
                    [(w, float(a)) for w, a in top_contribs]
                )

                hits.append(
                    SignalHit(
                        signal_id=self.id,
                        wallet_address="",
                        market_id=mid,
                        market_title=str(anchor.get("title", "")),
                        event_slug=str(anchor.get("eventSlug", "")),
                        score=min(
                            1.0,
                            0.4
                            + 0.1 * (len(wallet_notional) - self.min_wallets)
                            + min(float(notional) / 500_000, 0.5),
                        ),
                        row={
                            "market_id": mid,
                            "market_title": str(anchor.get("title", "")),
                            "market_url": f"https://polymarket.com/event/{anchor.get('eventSlug', '')}",
                            "window_start_ts": anchor_ts,
                            "window_start_fmt": datetime.fromtimestamp(
                                anchor_ts, UTC
                            ).strftime("%Y-%m-%d %H:%M UTC"),
                            "wallet_count": len(wallet_notional),
                            "span_s": last_ts - anchor_ts,
                            "span_s_fmt": f"{last_ts - anchor_ts}s",
                            "combined_notional": float(notional),
                            "combined_notional_fmt": _money(notional),
                            "top_wallets": [
                                {
                                    "address": w,
                                    "amount": float(a),
                                }
                                for w, a in top_contribs
                            ],
                            "top_wallets_fmt": top_wallets_fmt,
                        },
                    )
                )

        hits.sort(
            key=lambda h: (h.row["wallet_count"], h.row["combined_notional"]),
            reverse=True,
        )
        hits = hits[: self.top_n]

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
