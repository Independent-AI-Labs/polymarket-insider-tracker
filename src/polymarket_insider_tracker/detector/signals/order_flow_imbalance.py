"""Signal 02-A — Order-flow imbalance.

Spec: docs/signals/02-microstructure.md § 02-A.

Per-market sum of signed notional, normalized to [-1, +1]. A
sustained |OFI| ≥ threshold on a market with enough trades is the
flag.

Market-level signal — BUT now includes the top contributing
wallets per market so the reader can drill from "OFI fired on X"
to "these are the wallets that pushed it."
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .base import (
    ColumnSpec,
    Signal,
    SignalContext,
    SignalHit,
    _money,
    _pct,
    _short_wallet,
)
from .gates import DEFAULT_GATES, GateConfig, passes_all


class OrderFlowImbalanceSignal(Signal):
    id = "02-A-order-flow-imbalance"
    name = "Order-flow imbalance"
    category = "microstructure"
    reliability_band = "medium"
    hide_when_empty = True

    def __init__(
        self,
        *,
        threshold: float = 0.70,
        min_trades: int = 10,
        top_contributors: int = 3,
        top_n: int = 5,
        gate_config: GateConfig | None = None,
    ) -> None:
        self.threshold = threshold
        self.min_trades = min_trades
        self.top_contributors = top_contributors
        self.top_n = top_n
        self.gates = gate_config or DEFAULT_GATES
        self.description = (
            f"Markets where ≥ {self.threshold:.0%} of the window's "
            "notional flowed to the same side — the Hasbrouck signed-"
            "trade indicator of sustained directional pressure. "
            f"Minimum {self.min_trades} trades; excludes markets at "
            f"extreme prices (< {self.gates.min_price} or > "
            f"{self.gates.max_price}), markets closing within "
            f"{self.gates.min_hours_to_close:.0f} h, and sub-"
            f"{self.gates.min_life_hours:.0f} h-lifespan markets. "
            f"Top {self.top_contributors} contributing wallets "
            "surfaced per market."
        )

    def columns(self) -> list[ColumnSpec]:
        return [
            ColumnSpec("market_title", "Market", "left", "text",
                       link_field="market_url", width_hint="34%"),
            ColumnSpec("dominant_side", "Side", "left", "text"),
            ColumnSpec("imbalance_fmt", "Imbalance", "right", "percent"),
            ColumnSpec("trade_count", "Trades", "right", "int"),
            ColumnSpec("net_notional_fmt", "Net notional", "right", "money"),
            ColumnSpec("top_wallets_fmt", "Top contributors", "left", "text"),
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

        by_market: dict[str, dict[str, Any]] = {}
        # wallet-level contributions per market so we can surface the top contributors.
        by_wallet_market: dict[tuple[str, str], Decimal] = {}

        for t in context.trades:
            mid = str(t.get("conditionId", "")).lower()
            if not mid or mid not in eligible:
                continue
            entry = by_market.setdefault(
                mid,
                {
                    "title": str(t.get("title", "")),
                    "event_slug": str(t.get("eventSlug", "")),
                    "buy_notional": Decimal("0"),
                    "sell_notional": Decimal("0"),
                    "trade_count": 0,
                },
            )
            n = Decimal(str(t.get("size", 0))) * Decimal(str(t.get("price", 0)))
            entry["trade_count"] += 1
            side = str(t.get("side", "")).upper()
            if side == "BUY":
                entry["buy_notional"] += n
                signed = n
            else:
                entry["sell_notional"] += n
                signed = -n
            wallet = str(t.get("proxyWallet", "")).lower()
            if wallet:
                key = (mid, wallet)
                by_wallet_market[key] = by_wallet_market.get(key, Decimal("0")) + signed

        hits: list[SignalHit] = []
        for mid, e in by_market.items():
            if e["trade_count"] < self.min_trades:
                continue
            total = e["buy_notional"] + e["sell_notional"]
            if total == 0:
                continue
            dominant = max(e["buy_notional"], e["sell_notional"])
            imbalance = float(dominant / total)
            if imbalance < self.threshold:
                continue
            side = "BUY" if e["buy_notional"] >= e["sell_notional"] else "SELL"
            net = e["buy_notional"] - e["sell_notional"]
            # Top contributors on the dominant side.
            sign = 1 if side == "BUY" else -1
            contribs = [
                (wallet, amount)
                for (m, wallet), amount in by_wallet_market.items()
                if m == mid and (amount * sign > 0)
            ]
            contribs.sort(key=lambda p: abs(p[1]), reverse=True)
            top_contribs = contribs[: self.top_contributors]
            top_wallets_fmt = ", ".join(
                f"{_short_wallet(w)} ({_money(abs(a))})"
                for w, a in top_contribs
            ) or "—"

            hits.append(
                SignalHit(
                    signal_id=self.id,
                    wallet_address="",
                    market_id=mid,
                    market_title=e["title"],
                    event_slug=e["event_slug"],
                    score=min(1.0, (imbalance - self.threshold) / (1 - self.threshold)),
                    row={
                        "market_id": mid,
                        "market_title": e["title"],
                        "market_url": f"https://polymarket.com/event/{e['event_slug']}",
                        "dominant_side": side,
                        "imbalance": imbalance,
                        "imbalance_fmt": _pct(imbalance),
                        "trade_count": e["trade_count"],
                        "net_notional": float(net),
                        "net_notional_fmt": _money(net),
                        "top_wallets": [
                            {
                                "address": w,
                                "amount": float(abs(a)),
                                "side": "BUY" if a > 0 else "SELL",
                            }
                            for w, a in top_contribs
                        ],
                        "top_wallets_fmt": top_wallets_fmt,
                    },
                )
            )

        hits.sort(key=lambda h: (h.score, abs(h.row["net_notional"])), reverse=True)
        hits = hits[: self.top_n]

        if hits:
            top = hits[0]
            fragment = (
                f"{top.row['imbalance_fmt']} one-sided on "
                f"<em>{top.market_title}</em> "
                f"({top.row['net_notional_fmt']} {top.row['dominant_side']})"
            )
            hits[0] = SignalHit(**{**hits[0].__dict__, "headline_fragment": fragment})
        return hits
