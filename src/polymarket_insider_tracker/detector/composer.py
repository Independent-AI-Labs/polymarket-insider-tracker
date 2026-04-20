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
from .signals.base import (
    category_badges_html,
    category_palette,
    signal_badges_html,
)

LOG = logging.getLogger(__name__)

HEADLINE_FRAGMENT_LIMIT = 2
# A market is "promoted" when this many distinct signal CATEGORIES
# (informed_flow, microstructure, volume_liquidity, ...) fire on it.
PROMOTION_MIN_CATEGORIES = 2


@dataclass
class PromotedMarket:
    """A market where multiple signal categories fired."""

    market_id: str
    market_title: str
    market_url: str
    categories: list[str]
    signal_names: list[str]
    # (signal_name, category) tuples so downstream renderers can
    # produce colored badges — one source of truth for palette.
    signals_with_category: list[tuple[str, str]] = field(default_factory=list)
    total_notional: float = 0.0
    hit_details: list[dict[str, Any]] = field(default_factory=list)

    @property
    def category_badges_html(self) -> str:
        return category_badges_html(self.categories)

    @property
    def signal_badges_html(self) -> str:
        return signal_badges_html(self.signals_with_category)


@dataclass
class WalletWatch:
    """A wallet that appeared in at least one flagged activity."""

    address: str
    address_display: str
    profile_url: str
    market_count: int
    markets: list[dict[str, Any]]
    total_notional: float
    signal_names: set[str]
    categories: set[str]
    first_seen_fmt: str = ""
    is_fresh: bool = False

    @property
    def priority_score(self) -> float:
        return (
            100 * self.market_count
            + 10 * len(self.categories)
            + self.total_notional / 10_000
        )

    @property
    def category_badges_html(self) -> str:
        return category_badges_html(
            sorted(self.categories, key=lambda c: CATEGORY_ORDER.index(c)
                   if c in CATEGORY_ORDER else 99)
        )


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

    # Cross-signal promotion — round-2 fix.
    promoted = _compute_promoted_markets(all_hits)

    # Wallet-centric recurrence — round-3 fix. Flips the report
    # from market-centric to analyst-centric.
    wallets_to_watch = _compute_wallets_to_watch(all_hits, promoted)

    headline = _compose_headline(all_hits, promoted, wallets_to_watch, context)
    summary = _compose_summary(context, all_hits, promoted, wallets_to_watch)
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
    # Attach round-2/3 outputs as attributes — consumers read via
    # getattr so the base DailyReport dataclass stays minimal.
    report.promoted_markets = promoted  # type: ignore[attr-defined]
    report.wallets_to_watch = wallets_to_watch  # type: ignore[attr-defined]
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
        signals_with_cat: dict[str, str] = {}  # name → category
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
                    signals_with_cat[signal.name] = signal.category
                    break
        promoted.append(
            PromotedMarket(
                market_id=mid,
                market_title=e["market_title"],
                market_url=e["market_url"],
                categories=sorted(e["categories"]),
                signal_names=sorted(e["signal_names"]),
                signals_with_category=sorted(signals_with_cat.items()),
                total_notional=e["total_notional"],
                hit_details=hit_details,
            )
        )
    promoted.sort(
        key=lambda p: (len(p.categories), p.total_notional), reverse=True
    )
    return promoted


def _compute_wallets_to_watch(
    hits: list[SignalHit],
    promoted: list[PromotedMarket],
) -> list[WalletWatch]:
    """Aggregate every wallet that appeared in ANY flagged activity.

    Sources the wallet list from:
      - explicit wallet_address on signal hits (fresh-wallet, unusual-size)
      - top_wallets arrays inside market-level hits (OFI, clusters,
        volume velocity) — the reader doesn't want a market-level
        contributor-list to be invisible at the wallet-centric view.
    """
    by_wallet: dict[str, dict[str, Any]] = {}
    promoted_market_ids = {p.market_id for p in promoted}

    for hit in hits:
        signal_meta = None
        for signal in REGISTRY:
            if signal.id == hit.signal_id:
                signal_meta = signal
                break
        if signal_meta is None:
            continue

        # 1) Explicit wallet on the hit (wallet-level signals).
        if hit.wallet_address:
            _upsert_wallet(
                by_wallet,
                wallet=hit.wallet_address,
                hit=hit,
                signal_meta=signal_meta,
                notional=_money_from_row(hit.row),
                market_promoted=hit.market_id.lower() in promoted_market_ids,
                role="primary",
                first_seen_fmt=hit.row.get("first_seen_fmt", "") if hit.signal_id.startswith("01-A") else "",
                is_fresh=hit.signal_id.startswith("01-A"),
            )

        # 2) Contributor lists on market-level hits.
        for contrib in hit.row.get("top_wallets", []) or []:
            addr = str(contrib.get("address", "")).lower()
            amount = float(contrib.get("amount", 0))
            if not addr or amount <= 0:
                continue
            _upsert_wallet(
                by_wallet,
                wallet=addr,
                hit=hit,
                signal_meta=signal_meta,
                notional=amount,
                market_promoted=hit.market_id.lower() in promoted_market_ids,
                role="contributor",
                first_seen_fmt="",
                is_fresh=False,
            )

    watches: list[WalletWatch] = []
    for addr, e in by_wallet.items():
        markets_list = sorted(
            e["markets"].values(),
            key=lambda m: m["notional"],
            reverse=True,
        )
        # Pre-render signal-name badges per market (both email and
        # PDF consume this verbatim).
        for m in markets_list:
            sig_items = sorted(m["signals_with_category"].items())
            m["signals_fmt"] = ", ".join(n for n, _ in sig_items)
            m["signals_badges_html"] = signal_badges_html(sig_items)
            m["signals"] = {n for n, _ in sig_items}  # legacy shape
        watches.append(
            WalletWatch(
                address=addr,
                address_display=f"{addr[:6]}…{addr[-4:]}",
                profile_url=f"https://polymarket.com/profile/{addr}",
                market_count=len(markets_list),
                markets=markets_list,
                total_notional=e["total_notional"],
                signal_names=e["signal_names"],
                categories=e["categories"],
                first_seen_fmt=e["first_seen_fmt"],
                is_fresh=e["is_fresh"],
            )
        )
    watches.sort(key=lambda w: w.priority_score, reverse=True)
    return watches


def _upsert_wallet(
    acc: dict[str, dict[str, Any]],
    *,
    wallet: str,
    hit: SignalHit,
    signal_meta: Any,
    notional: float,
    market_promoted: bool,
    role: str,
    first_seen_fmt: str,
    is_fresh: bool,
) -> None:
    entry = acc.setdefault(
        wallet,
        {
            "markets": {},
            "total_notional": 0.0,
            "signal_names": set(),
            "categories": set(),
            "first_seen_fmt": "",
            "is_fresh": False,
        },
    )
    mid = hit.market_id.lower() if hit.market_id else ""
    market_slot = entry["markets"].setdefault(
        mid,
        {
            "market_id": mid,
            "title": hit.market_title,
            "url": f"https://polymarket.com/event/{hit.event_slug}" if hit.event_slug else "",
            "signals_with_category": {},  # name → category
            "roles": set(),
            "promoted": market_promoted,
            "notional": 0.0,
        },
    )
    market_slot["signals_with_category"][signal_meta.name] = signal_meta.category
    market_slot["roles"].add(role)
    market_slot["notional"] += notional
    market_slot["promoted"] = market_slot["promoted"] or market_promoted
    entry["total_notional"] += notional
    entry["signal_names"].add(signal_meta.name)
    entry["categories"].add(signal_meta.category)
    if first_seen_fmt and not entry["first_seen_fmt"]:
        entry["first_seen_fmt"] = first_seen_fmt
    entry["is_fresh"] = entry["is_fresh"] or is_fresh


def _money_from_row(row: dict[str, Any]) -> float:
    for key in ("notional", "net_notional", "combined_notional", "volume_24h"):
        v = row.get(key)
        if v is not None:
            try:
                return float(abs(v))
            except (TypeError, ValueError):
                pass
    return 0.0


def _compose_headline(
    hits: list[SignalHit],
    promoted: list[PromotedMarket],
    watches: list[WalletWatch],
    context: SignalContext,
) -> str:
    """Headline sentence — prefer promoted markets over single-signal hits."""
    # Strongest possible headline: a wallet that appears on ≥ 2
    # markets today. That's the clearest insider-adjacent tell we
    # can report without outcome data.
    multi_market_watches = [w for w in watches if w.market_count >= 2]
    if multi_market_watches:
        top = multi_market_watches[0]
        titles = [m["title"] for m in top.markets[:3]]
        title_fmt = " / ".join(f"<em>{t}</em>" for t in titles)
        fresh_tag = (
            f" — fresh wallet ({top.first_seen_fmt})"
            if top.is_fresh else ""
        )
        return (
            f"<strong>Cross-market wallet:</strong> "
            f"<code>{top.address_display}</code>{fresh_tag} "
            f"active on {top.market_count} markets "
            f"({_money_short(top.total_notional)} total): {title_fmt}."
        )

    if promoted:
        top = promoted[:HEADLINE_FRAGMENT_LIMIT]
        fragments = []
        for p in top:
            n_cat = len(p.categories)
            cats_fmt = ", ".join(p.categories)
            fragments.append(
                f"<em>{p.market_title}</em> fired "
                f"{n_cat} signal categor{'ies' if n_cat != 1 else 'y'} "
                f"({cats_fmt}) — "
                f"{_money_short(p.total_notional)} flagged"
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
    watches: list[WalletWatch],
) -> list[tuple[str, str]]:
    n_trades = len(context.trades)
    wallets = {str(t.get("proxyWallet", "")).lower() for t in context.trades}
    wallets.discard("")
    total_notional = sum(
        Decimal(str(t.get("size", 0))) * Decimal(str(t.get("price", 0)))
        for t in context.trades
    )
    # Fired signals rendered as badges, ordered by category order.
    sig_seen: dict[str, str] = {}
    for h in hits:
        for s in REGISTRY:
            if s.id == h.signal_id and s.name not in sig_seen:
                sig_seen[s.name] = s.category
                break
    signal_names_sorted = sorted(
        sig_seen.items(),
        key=lambda kv: (
            CATEGORY_ORDER.index(kv[1]) if kv[1] in CATEGORY_ORDER else 99,
            kv[0],
        ),
    )
    signals_cell = (
        signal_badges_html(signal_names_sorted) if signal_names_sorted
        else '<span style="color:#888">none</span>'
    )
    cross_market_wallets = sum(1 for w in watches if w.market_count >= 2)
    return [
        ("Trades observed", f"{n_trades:,}"),
        ("Unique wallets", f"{len(wallets):,}"),
        ("Total notional", _money_short(total_notional)),
        ("Signals firing", signals_cell),
        ("Cross-signal markets", str(len(promoted))),
        ("Cross-market wallets (≥ 2)", str(cross_market_wallets)),
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
