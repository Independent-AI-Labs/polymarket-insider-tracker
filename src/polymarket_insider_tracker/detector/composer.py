"""Composer — runs every registered signal and assembles a DailyReport.

The newsletter script calls `compose(context)` once and hands the
returned `DailyReport` to the Tera template. Everything downstream
is data-driven: adding a signal adds a section, modifying a signal's
columns changes what the reader sees, no template edits required.

Headline, summary stats, glossary — all derived here from the
registered signals + their hits. No hardcoded copy downstream.
"""

from __future__ import annotations

import logging
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

# Maximum headline fragments concatenated into one sentence.
HEADLINE_FRAGMENT_LIMIT = 2


def compose(context: SignalContext, *, source_label: str = "") -> DailyReport:
    """Run every registered signal and build the report.

    Signals are called in registry order (which matches category
    display order). Each signal produces a list of hits; we convert
    those into a SectionSpec via the signal's own `section_spec`
    hook — which by default uses its `columns()` + `description` +
    `name` — so the signal itself owns its rendering shape.
    """
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

    # Order sections by taxonomy category, then by registry order.
    sections.sort(
        key=lambda s: (
            CATEGORY_ORDER.index(s.category)
            if s.category in CATEGORY_ORDER
            else len(CATEGORY_ORDER),
            s.signal_id,
        )
    )

    # ── Headline ───────────────────────────────────────────
    headline = _compose_headline(all_hits, context)

    # ── Summary stats ──────────────────────────────────────
    summary = _compose_summary(context, all_hits)

    # ── Glossary ───────────────────────────────────────────
    glossary = [
        (s.name, s.reliability_band, s.description)
        for s in REGISTRY
    ]

    # ── Raw alerts (CSV attachment) ────────────────────────
    raw_alerts = _compose_raw_alerts(context)

    return DailyReport(
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


def _compose_headline(
    hits: list[SignalHit], context: SignalContext
) -> str:
    """Concatenate the top-scored headline fragments.

    Each signal marks at most one hit with a `headline_fragment`
    string. We pick the top HEADLINE_FRAGMENT_LIMIT fragments by
    hit score and join them with "·". If no signal produced a
    fragment, the headline reports pure volume stats.
    """
    framed = [h for h in hits if h.headline_fragment]
    framed.sort(key=lambda h: h.score, reverse=True)
    top = framed[:HEADLINE_FRAGMENT_LIMIT]
    if top:
        joined = " · ".join(h.headline_fragment for h in top)
        return f"<strong>Today:</strong> {joined}."

    # Fallback when no signal fired — be honest about the quiet
    # window rather than inventing a headline.
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
    context: SignalContext, hits: list[SignalHit]
) -> list[tuple[str, str]]:
    """Cover-strip numbers. Derived from trades + hits, not hardcoded."""
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
    ]


def _compose_raw_alerts(context: SignalContext) -> list[dict[str, Any]]:
    """The primary-data CSV rows. Every trade in the window."""
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


def _money_short(value: Decimal | float) -> str:
    v = float(value)
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:,.0f}"
