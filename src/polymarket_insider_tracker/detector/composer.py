"""Composer — runs every registered signal and assembles a DailyReport.

Since the round-2 fix set, the composer also produces a
`promoted_markets` list: markets where ≥ N distinct signal
categories fired on the same `market_id`. Those are the rows a
reader cares about most — single-signal hits are informational,
cross-signal hits are actionable. The headline prefers promoted
markets when any exist.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from .signals import (
    CATEGORY_ORDER,
    DailyReport,
    REGISTRY,
    SignalContext,
    SignalHit,
)

LOG = logging.getLogger(__name__)

HEADLINE_FRAGMENT_LIMIT = 2
# A market is "promoted" when this many distinct signal CATEGORIES
# (informed_flow, microstructure, volume_liquidity, ...) fire on it.
PROMOTION_MIN_CATEGORIES = 2


@dataclass
class PromotedMarket:
    """A market where multiple signal categories fired.

    Distinct from a single-signal hit — promoted markets are what
    M. actually reads. The PDF renders them in their own tier
    before the per-signal log, and the headline prefers them.
    """

    market_id: str
    market_title: str
    market_url: str
    categories: list[str]
    signal_names: list[str]
    total_notional: float
    hit_details: list[dict[str, Any]] = field(default_factory=list)


def compose(context: SignalContext, *, source_label: str = "") -> DailyReport:
    all_hits: list[SignalHit] = []
    sections = []
    for signal in REGISTRY:
        try:
            hits = signal.compute(context)
        except Exception:
            LOG.exception("signal %s failed; skipping", signal.id)
            hits = []
        all_hits.extend(hits)
        spec = signal.section_spec(hits)
        if not hits and spec.hide_when_empty:
            continue
        sections.append(spec)

    sections.sort(
        key=lambda s: (
            CATEGORY_ORDER.index(s.category)
            if s.category in CATEGORY_ORDER
            else len(CATEGORY_ORDER),
            s.signal_id,
        )
    )

    # Cross-signal promotion — the key round-2 fix.
    promoted = _compute_promoted_markets(all_hits)

    headline = _compose_headline(all_hits, promoted, context)
    summary = _compose_summary(context, all_hits, promoted)
    glossary = [
        (s.name, s.reliability_band, s.description) for s in REGISTRY
    ]
    raw_alerts = _compose_raw_alerts(context)

    report = DailyReport(
        date=context.edition_date,
        window_start=context.window_start or datetime.now(UTC),
        window_end=context.window_end or datetime.now(UTC),
        edition_id=f"daily-{context.edition_date}",
        summary=summary,
        headline=headline,
        sections=sections,
        glossary=glossary,
        raw_alerts=raw_alerts,
        source_label=source_label,
    )
    # Promoted markets aren't in the base DailyReport dataclass (it's
    # defined in signals/base.py and deliberately minimal). Attach as
    # an attribute; the PDF and newsletter payload both read it via
    # getattr.
    report.promoted_markets = promoted  # type: ignore[attr-defined]
    return report


def _compute_promoted_markets(hits: list[SignalHit]) -> list[PromotedMarket]:
    """Group hits by market_id. Flag markets where ≥ N distinct
    signal categories fire.
    """
    by_market: dict[str, dict[str, Any]] = {}
    for hit in hits:
        mid = (hit.market_id or "").lower()
        if not mid:
            continue
        entry = by_market.setdefault(
            mid,
            {
                "market_id": mid,
                "market_title": hit.market_title,
                "market_url": f"https://polymarket.com/event/{hit.event_slug}"
                if hit.event_slug
                else "",
                "categories": set(),
                "signal_names": set(),
                "hits": [],
                "total_notional": 0.0,
            },
        )
        # Find the signal's category by ID prefix (matches registry).
        for signal in REGISTRY:
            if signal.id == hit.signal_id:
                entry["categories"].add(signal.category)
                entry["signal_names"].add(signal.name)
                break
        entry["hits"].append(hit)
        # Aggregate notional from whatever money field the row has.
        money = (
            hit.row.get("notional")
            or hit.row.get("net_notional")
            or hit.row.get("combined_notional")
            or hit.row.get("volume_24h")
            or 0
        )
        try:
            entry["total_notional"] += float(abs(money))
        except (TypeError, ValueError):
            pass

    promoted: list[PromotedMarket] = []
    for mid, e in by_market.items():
        if len(e["categories"]) < PROMOTION_MIN_CATEGORIES:
            continue
        hit_details = []
        for h in e["hits"]:
            for signal in REGISTRY:
                if signal.id == h.signal_id:
                    hit_details.append(
                        {
                            "signal_name": signal.name,
                            "signal_id": h.signal_id,
                            "category": signal.category,
                            "wallet_address": h.wallet_address,
                            "row": h.row,
                        }
                    )
                    break
        promoted.append(
            PromotedMarket(
                market_id=mid,
                market_title=e["market_title"],
                market_url=e["market_url"],
                categories=sorted(e["categories"]),
                signal_names=sorted(e["signal_names"]),
                total_notional=e["total_notional"],
                hit_details=hit_details,
            )
        )
    promoted.sort(
        key=lambda p: (len(p.categories), p.total_notional), reverse=True
    )
    return promoted


def _compose_headline(
    hits: list[SignalHit],
    promoted: list[PromotedMarket],
    context: SignalContext,
) -> str:
    """Headline sentence — prefer promoted markets over single-signal hits."""
    if promoted:
        # Up to HEADLINE_FRAGMENT_LIMIT promoted markets, ranked.
        top = promoted[:HEADLINE_FRAGMENT_LIMIT]
        fragments = []
        for p in top:
            fragments.append(
                f"<em>{p.market_title}</em> fired "
                f"{len(p.categories)} signal categories "
                f"({', '.join(p.signal_names)}) — "
                f"{_money_short(p.total_notional)} notional"
            )
        joined = " · ".join(fragments)
        return f"<strong>Cross-signal today:</strong> {joined}."

    # No cross-signal promotions — fall back to per-signal fragments.
    framed = [h for h in hits if h.headline_fragment]
    framed.sort(key=lambda h: h.score, reverse=True)
    top = framed[:HEADLINE_FRAGMENT_LIMIT]
    if top:
        joined = " · ".join(h.headline_fragment for h in top)
        return (
            "<strong>Single-signal only today</strong> "
            "(no market fired ≥ 2 signal categories). "
            f"Notable: {joined}."
        )

    n_trades = len(context.trades)
    if n_trades == 0:
        return (
            "<strong>Quiet window.</strong> No size-meaningful trades "
            "observed — data-api returned no rows."
        )
    total_notional = sum(
        Decimal(str(t.get("size", 0))) * Decimal(str(t.get("price", 0)))
        for t in context.trades
    )
    return (
        f"<strong>Quiet signals.</strong> {n_trades:,} trades "
        f"({_money_short(total_notional)} notional) observed but no "
        "signal thresholds crossed — a null is also a data point."
    )


def _compose_summary(
    context: SignalContext,
    hits: list[SignalHit],
    promoted: list[PromotedMarket],
) -> list[tuple[str, str]]:
    n_trades = len(context.trades)
    wallets = {str(t.get("proxyWallet", "")).lower() for t in context.trades}
    wallets.discard("")
    total_notional = sum(
        Decimal(str(t.get("size", 0))) * Decimal(str(t.get("price", 0)))
        for t in context.trades
    )
    n_signals_fired = len({h.signal_id for h in hits})
    return [
        ("Trades observed", f"{n_trades:,}"),
        ("Unique wallets", f"{len(wallets):,}"),
        ("Total notional", _money_short(total_notional)),
        ("Signals firing", str(n_signals_fired)),
        ("Cross-signal markets", str(len(promoted))),
    ]


def _compose_raw_alerts(context: SignalContext) -> list[dict[str, Any]]:
    out = []
    for t in context.trades:
        size = Decimal(str(t.get("size", 0)))
        price = Decimal(str(t.get("price", 0)))
        out.append(
            {
                "ts": datetime.fromtimestamp(int(t.get("timestamp", 0)), UTC).isoformat(),
                "wallet": t.get("proxyWallet", ""),
                "market_id": t.get("conditionId", ""),
                "market_slug": t.get("eventSlug", ""),
                "title": t.get("title", ""),
                "side": t.get("side", ""),
                "size": str(size),
                "price": str(price),
                "notional": str(size * price),
                "tx_hash": t.get("transactionHash", ""),
            }
        )
    return out


def _money_short(value) -> str:
    v = float(value)
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:,.0f}"
