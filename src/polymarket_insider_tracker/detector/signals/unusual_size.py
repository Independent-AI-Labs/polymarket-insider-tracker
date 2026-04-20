"""Signal 01-B — Unusual size (single-fill + stealth variants).

Spec: docs/signals/01-informed-flow.md § 01-B.

Variant A flags a single trade whose notional dwarfs the market's
own 24h trade-size distribution (the Fredi9999 pattern). Variant B
flags the Barclay-Warner (1993) stealth pattern — one wallet
making many mid-size trades on the same market inside a 4h window
that SUM to a dominant one-sided position.

Both variants emit rows in the same section; `variant` column
tells M. which pattern fired.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from .base import (
    ColumnSpec,
    Signal,
    SignalContext,
    SignalHit,
    _money,
    _short_wallet,
)

VARIANT_A_MULTIPLE = 5.0  # single fill ≥ 5× market p90
VARIANT_B_MIN_TRADES = 5
VARIANT_B_WINDOW_HOURS = 4
VARIANT_B_DOMINANCE = 0.80
VARIANT_B_NOTIONAL_MULTIPLE = 3.0

# Minimum trades for a market to even participate in p90 calc.
P90_MIN_TRADES = 10


class UnusualSizeSignal(Signal):
    id = "01-B-unusual-size"
    name = "Unusual-size fills"
    category = "informed_flow"
    description = (
        "Trades whose notional is an outlier on the market's own "
        "24h distribution (Variant A — the single-fill pattern) or "
        "groups of mid-size same-wallet same-market trades that "
        "stealth-accumulate a dominant position (Variant B, "
        "Barclay & Warner 1993)."
    )
    reliability_band = "medium"
    hide_when_empty = True

    def columns(self) -> list[ColumnSpec]:
        return [
            ColumnSpec("wallet_display", "Wallet", "left", "wallet",
                       link_field="wallet_url"),
            ColumnSpec("market_title", "Market", "left", "text",
                       link_field="market_url", width_hint="38%"),
            ColumnSpec("side", "Side", "left", "text"),
            ColumnSpec("notional_fmt", "Notional", "right", "money"),
            ColumnSpec("variant_display", "Variant", "left", "text"),
            ColumnSpec("context_fmt", "vs market p90", "right", "text"),
        ]

    def compute(self, context: SignalContext) -> list[SignalHit]:
        if not context.trades:
            return []

        # Precompute per-market p90 single-trade notional.
        by_market: dict[str, list[Decimal]] = {}
        for t in context.trades:
            mid = str(t.get("conditionId", ""))
            if not mid:
                continue
            notional = Decimal(str(t.get("size", 0))) * Decimal(
                str(t.get("price", 0))
            )
            by_market.setdefault(mid, []).append(notional)

        p90_by_market: dict[str, Decimal] = {}
        for mid, notionals in by_market.items():
            if len(notionals) < P90_MIN_TRADES:
                continue
            notionals.sort()
            k = int(0.90 * (len(notionals) - 1))
            p90_by_market[mid] = notionals[k]

        # Variant A — single-fill dominance.
        hits: list[SignalHit] = []
        for t in context.trades:
            mid = str(t.get("conditionId", ""))
            wallet = str(t.get("proxyWallet", "")).lower()
            if not mid or not wallet:
                continue
            p90 = p90_by_market.get(mid)
            if p90 is None or p90 <= 0:
                continue
            notional = Decimal(str(t.get("size", 0))) * Decimal(
                str(t.get("price", 0))
            )
            multiple = float(notional / p90)
            if multiple < VARIANT_A_MULTIPLE:
                continue
            hits.append(
                _make_hit(
                    self.id,
                    t,
                    wallet,
                    notional,
                    variant="A",
                    variant_display="single-fill",
                    context_fmt=f"{multiple:.1f}× p90",
                    score=min(1.0, 0.5 + min(multiple / 20, 0.5)),
                )
            )

        # Variant B — stealth clustering.
        variant_b = _compute_stealth_clusters(
            context.trades, p90_by_market, signal_id=self.id
        )
        hits.extend(variant_b)

        # Dedupe: if variant A and variant B both fired for the same
        # (wallet, market), keep the one with higher score. Variant
        # A's single-fill is usually stronger signal.
        by_key: dict[tuple[str, str], SignalHit] = {}
        for h in hits:
            key = (h.wallet_address, h.market_id)
            if key not in by_key or h.score > by_key[key].score:
                by_key[key] = h
        deduped = sorted(by_key.values(), key=lambda h: h.score, reverse=True)
        deduped = deduped[:5]

        # Headline fragment: the single biggest.
        if deduped:
            top = deduped[0]
            fragment = (
                f"single {_money(top.row['notional'])} fill on "
                f"<em>{top.market_title}</em> "
                f"({top.row['context_fmt']})"
            )
            deduped[0] = SignalHit(**{**deduped[0].__dict__, "headline_fragment": fragment})

        return deduped


def _make_hit(
    signal_id: str,
    trade: dict[str, Any],
    wallet: str,
    notional: Decimal,
    *,
    variant: str,
    variant_display: str,
    context_fmt: str,
    score: float,
) -> SignalHit:
    return SignalHit(
        signal_id=signal_id,
        wallet_address=wallet,
        market_id=str(trade.get("conditionId", "")),
        market_title=str(trade.get("title", "")),
        event_slug=str(trade.get("eventSlug", "")),
        score=score,
        row={
            "wallet_address": wallet,
            "wallet_display": _short_wallet(wallet),
            "wallet_url": f"https://polymarket.com/profile/{wallet}",
            "market_title": str(trade.get("title", "")),
            "market_url": f"https://polymarket.com/event/{trade.get('eventSlug', '')}",
            "side": str(trade.get("side", "")),
            "notional": float(notional),
            "notional_fmt": _money(notional),
            "variant": variant,
            "variant_display": variant_display,
            "context_fmt": context_fmt,
        },
    )


def _compute_stealth_clusters(
    trades: list[dict[str, Any]],
    p90_by_market: dict[str, Decimal],
    *,
    signal_id: str,
) -> list[SignalHit]:
    """Barclay-Warner stealth pattern — same wallet, same market, many
    mid-size trades in a 4h window summing one-sided above threshold.
    """
    window_s = VARIANT_B_WINDOW_HOURS * 3600

    # Group by (wallet, market) and sort per group.
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for t in trades:
        mid = str(t.get("conditionId", ""))
        wallet = str(t.get("proxyWallet", "")).lower()
        if not mid or not wallet:
            continue
        by_key.setdefault((wallet, mid), []).append(t)

    hits: list[SignalHit] = []
    for (wallet, mid), group in by_key.items():
        if len(group) < VARIANT_B_MIN_TRADES:
            continue
        group.sort(key=lambda r: int(r.get("timestamp", 0)))
        # Sliding window over the 4h interval.
        for i in range(len(group)):
            window_trades: list[dict[str, Any]] = []
            t0 = int(group[i].get("timestamp", 0))
            for j in range(i, len(group)):
                ts = int(group[j].get("timestamp", 0))
                if ts - t0 > window_s:
                    break
                window_trades.append(group[j])
            if len(window_trades) < VARIANT_B_MIN_TRADES:
                continue
            buy_notional = Decimal("0")
            sell_notional = Decimal("0")
            for t in window_trades:
                n = Decimal(str(t.get("size", 0))) * Decimal(str(t.get("price", 0)))
                if str(t.get("side", "")).upper() == "BUY":
                    buy_notional += n
                else:
                    sell_notional += n
            total = buy_notional + sell_notional
            if total == 0:
                continue
            dominance = float(max(buy_notional, sell_notional) / total)
            if dominance < VARIANT_B_DOMINANCE:
                continue
            p90 = p90_by_market.get(mid)
            if p90 is None or p90 <= 0:
                continue
            threshold = (
                Decimal(str(VARIANT_B_NOTIONAL_MULTIPLE))
                * p90
                * Decimal(len(window_trades))
                / Decimal(2)
            )
            if total < threshold:
                # Rough: require sum ≥ 3× the "typical wallet-day" on
                # this market, proxied by p90 × (trades / 2).
                continue
            side = "BUY" if buy_notional >= sell_notional else "SELL"
            multiple = float(total / (p90 * Decimal(len(window_trades)) / Decimal(2)))
            hits.append(
                _make_hit(
                    signal_id,
                    window_trades[0],
                    wallet,
                    total,
                    variant="B",
                    variant_display=f"stealth × {len(window_trades)}",
                    context_fmt=f"{multiple:.1f}× p90/trade",
                    score=min(1.0, 0.5 + (dominance - VARIANT_B_DOMINANCE) * 2.5),
                ),
            )
            break  # one cluster per (wallet, market) is enough
    return hits
