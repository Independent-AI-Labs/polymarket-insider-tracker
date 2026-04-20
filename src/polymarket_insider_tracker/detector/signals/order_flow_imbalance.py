"""Signal 02-A — Order-flow imbalance.

Spec: docs/signals/02-microstructure.md § 02-A.

Per-market sum of signed notional, normalized to [-1, +1]. A
sustained |OFI| ≥ 0.70 on a market with ≥ 10 trades is the flag.

Produces market-level rows (no wallet column).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .base import (
    ColumnSpec,
    Signal,
    SignalContext,
    SignalHit,
    _money,
    _pct,
)

OFI_THRESHOLD = 0.70
MIN_TRADES = 10
# Exclude markets closing within this many hours — resolution-drift
# creates mechanical OFI that isn't informational.
MIN_TIME_TO_CLOSE_HOURS = 24


class OrderFlowImbalanceSignal(Signal):
    id = "02-A-order-flow-imbalance"
    name = "Order-flow imbalance"
    category = "microstructure"
    description = (
        "Markets where ≥ 70 % of the window's notional flowed to the "
        "same side — the Hasbrouck-style signed-trade indicator of "
        "sustained directional pressure. Markets closing within 24 h "
        "excluded to filter mechanical resolution drift."
    )
    reliability_band = "medium"
    hide_when_empty = True

    def columns(self) -> list[ColumnSpec]:
        return [
            ColumnSpec("market_title", "Market", "left", "text",
                       link_field="market_url", width_hint="45%"),
            ColumnSpec("dominant_side", "Side", "left", "text"),
            ColumnSpec("imbalance_fmt", "Imbalance", "right", "percent"),
            ColumnSpec("trade_count", "Trades", "right", "int"),
            ColumnSpec("net_notional_fmt", "Net notional", "right", "money"),
        ]

    def compute(self, context: SignalContext) -> list[SignalHit]:
        if not context.trades:
            return []

        by_market: dict[str, dict[str, Any]] = {}
        for t in context.trades:
            mid = str(t.get("conditionId", ""))
            if not mid:
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
            if str(t.get("side", "")).upper() == "BUY":
                entry["buy_notional"] += n
            else:
                entry["sell_notional"] += n

        hits: list[SignalHit] = []
        now = context.window_end or datetime.now(timezone.utc)
        close_cutoff = now + timedelta(hours=MIN_TIME_TO_CLOSE_HOURS)

        for mid, e in by_market.items():
            if e["trade_count"] < MIN_TRADES:
                continue
            meta = context.market_meta.get(mid.lower()) or {}
            end_raw = str(meta.get("endDate", "") or meta.get("endDateIso", ""))
            if end_raw:
                try:
                    end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
                except ValueError:
                    end_dt = None
                if end_dt is not None and end_dt < close_cutoff:
                    continue
            total = e["buy_notional"] + e["sell_notional"]
            if total == 0:
                continue
            dominant = max(e["buy_notional"], e["sell_notional"])
            imbalance = float(dominant / total)
            if imbalance < OFI_THRESHOLD:
                continue
            side = "BUY" if e["buy_notional"] >= e["sell_notional"] else "SELL"
            net = e["buy_notional"] - e["sell_notional"]
            hits.append(
                SignalHit(
                    signal_id=self.id,
                    wallet_address="",
                    market_id=mid,
                    market_title=e["title"],
                    event_slug=e["event_slug"],
                    score=min(1.0, (imbalance - OFI_THRESHOLD) / (1 - OFI_THRESHOLD)),
                    row={
                        "market_title": e["title"],
                        "market_url": f"https://polymarket.com/event/{e['event_slug']}",
                        "dominant_side": side,
                        "imbalance": imbalance,
                        "imbalance_fmt": _pct(imbalance),
                        "trade_count": e["trade_count"],
                        "net_notional": float(net),
                        "net_notional_fmt": _money(net),
                    },
                )
            )

        hits.sort(key=lambda h: (h.score, abs(h.row["net_notional"])), reverse=True)
        hits = hits[:5]

        if hits:
            top = hits[0]
            fragment = (
                f"{top.row['imbalance_fmt']} one-sided on "
                f"<em>{top.market_title}</em> "
                f"({top.row['net_notional_fmt']} {top.row['dominant_side']})"
            )
            hits[0] = SignalHit(**{**hits[0].__dict__, "headline_fragment": fragment})
        return hits
