"""Pydantic v2 models for a daily metrics snapshot.

The snapshot captures what the composer produced for a single edition —
wallet-level and market-level aggregates plus per-signal hit counts —
in a typed, versioned form safe to round-trip through JSON. It does
NOT extend the composer's dataclasses; every value needed here is
derived in `snapshot_from_report` from fields already on `DailyReport`,
`WalletWatch`, `PromotedMarket`, and `SignalHit`.
"""

from __future__ import annotations

import contextlib
from collections import Counter
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from datetime import date

    from polymarket_insider_tracker.detector.signals.base import DailyReport


SCHEMA_VERSION = 1


class WalletMetric(BaseModel):
    """Per-wallet aggregates observed in this window.

    The fields here mirror what the composer's `WalletWatch` surfaces,
    plus a couple of cheaply-derived sums. Nothing is back-computed
    from chain data — if a field can't be derived from `WalletWatch`
    it stays absent rather than guessed.
    """

    model_config = ConfigDict(extra="forbid")

    address: str
    address_display: str = ""
    # Composer only exposes total_notional (absolute sum). We carry it
    # as notional_gross for symmetry with the literature vocabulary;
    # notional_net remains optional until the composer tracks signed
    # flow per wallet (it doesn't yet — see `_upsert_wallet`).
    notional_gross: float = 0.0
    notional_net: float | None = None
    trade_count: int = 0
    markets_touched: int = 0
    # Signal IDs the wallet hit (derived from WalletWatch.signal_names
    # via reverse lookup in snapshot_from_report).
    signals_fired: list[str] = Field(default_factory=list)
    # Category keys from WalletWatch.categories (stable enum in
    # CATEGORY_ORDER).
    categories_touched: list[str] = Field(default_factory=list)
    # Promoted-market count — wallets active on ≥ 1 cross-signal
    # promoted market are a priority for Phase 3 clustering.
    promoted_markets_touched: int = 0
    first_seen_in_window: datetime | None = None
    is_fresh: bool = False
    # Flat list of condition_ids this wallet touched this window.
    market_ids: list[str] = Field(default_factory=list)


class MarketMetric(BaseModel):
    """Per-market aggregates observed in this window."""

    model_config = ConfigDict(extra="forbid")

    condition_id: str
    title: str = ""
    event_slug: str = ""
    volume_window: float = 0.0
    last_trade_price: float | None = None
    # Signal IDs the market hit.
    signal_hits: list[str] = Field(default_factory=list)
    # Category keys — from the signals that fired.
    categories: list[str] = Field(default_factory=list)
    unique_wallets: int = 0
    trade_count: int = 0
    # True iff ≥ PROMOTION_MIN_CATEGORIES signal categories fired here.
    promoted: bool = False


class DailyMetricsSnapshot(BaseModel):
    """One edition's full metrics payload — write-once, read-many."""

    model_config = ConfigDict(extra="forbid")

    window_start: datetime
    window_end: datetime
    edition_id: str
    date: str  # YYYY-MM-DD — matches DailyReport.date
    source_label: str = ""
    schema_version: int = SCHEMA_VERSION

    # address → WalletMetric
    wallets: dict[str, WalletMetric] = Field(default_factory=dict)
    # condition_id → MarketMetric
    markets: dict[str, MarketMetric] = Field(default_factory=dict)
    # signal_id → number of hits in this window
    signal_counts: dict[str, int] = Field(default_factory=dict)
    # category → number of hits
    category_counts: dict[str, int] = Field(default_factory=dict)

    # Context-level stats the composer already computes.
    total_trades: int = 0
    total_notional: float = 0.0
    unique_wallets_in_window: int = 0


class MetricsIndex(BaseModel):
    """Compact per-edition summary for fast range scans.

    One line per edition in `<root>/index.jsonl`. Readers can grep /
    stream this without loading full snapshots. Useful for `list_range`
    and for dashboards that want a lightweight timeline.
    """

    model_config = ConfigDict(extra="forbid")

    date: str  # YYYY-MM-DD
    edition_id: str
    wallet_count: int
    market_count: int
    total_notional: float = 0.0
    # Relative path to the snapshot under `<root>`, e.g.
    # "snapshots/2026/2026-04-20.json".
    snapshot_file: str
    written_at: datetime

    def date_obj(self) -> date:
        """Parse `date` into a `datetime.date`. Convenience accessor."""
        from datetime import date as _date

        return _date.fromisoformat(self.date)


# ── Derivation helpers ──────────────────────────────────────────────


def _money_from_row(row: dict[str, Any]) -> float:
    """Copy of composer._money_from_row — one-line derivation so we
    don't reach into a private helper. Matches the composer's notional
    extraction order exactly.
    """
    for key in ("notional", "net_notional", "combined_notional", "volume_24h"):
        v = row.get(key)
        if v is not None:
            with contextlib.suppress(TypeError, ValueError):
                return float(abs(v))
    return 0.0


def snapshot_from_report(
    report: DailyReport,
    *,
    source_label: str = "",
) -> DailyMetricsSnapshot:
    """Project a `DailyReport` into a `DailyMetricsSnapshot`.

    Pure projection — does not mutate the report. Reads the composer's
    public surfaces (`sections`, `raw_alerts`) plus the two attributes
    the composer attaches post-construct (`promoted_markets`,
    `wallets_to_watch`). Anything missing from those surfaces is left
    at its model default.

    Kept in `metrics/models.py` so the import graph stays flat:
    detector doesn't import metrics, metrics imports detector types
    only under TYPE_CHECKING.
    """
    # Round-trip-safe: do the imports lazily so tests can stub out
    # the detector without pulling the whole registry.
    from polymarket_insider_tracker.detector.signals.registry import REGISTRY

    promoted_markets = getattr(report, "promoted_markets", None) or []
    wallets_to_watch = getattr(report, "wallets_to_watch", None) or []

    # name → signal_id reverse map for wallet signals_fired derivation.
    name_to_id = {s.name: s.id for s in REGISTRY}
    id_to_category = {s.id: s.category for s in REGISTRY}

    # ── Wallets ────────────────────────────────────────────────────
    promoted_mids = {p.market_id.lower() for p in promoted_markets}
    wallets: dict[str, WalletMetric] = {}
    for w in wallets_to_watch:
        addr = w.address.lower()
        market_ids = [str(m.get("market_id", "")).lower() for m in w.markets]
        promoted_touched = sum(
            1 for mid in market_ids if mid and mid in promoted_mids
        )
        signals_fired = sorted(
            {name_to_id[n] for n in w.signal_names if n in name_to_id}
        )
        # WalletWatch.markets carries per-market notional; sum of roles
        # inside a market slot gives trade-count-equivalent as used by
        # the composer's priority score (1 per signal-role, not per
        # on-chain trade — we faithfully record that rather than invent
        # a trade count the composer never computed).
        trade_count = sum(len(m.get("roles", set()) or []) for m in w.markets)
        wallets[addr] = WalletMetric(
            address=addr,
            address_display=w.address_display,
            notional_gross=float(w.total_notional),
            trade_count=trade_count,
            markets_touched=w.market_count,
            signals_fired=signals_fired,
            categories_touched=sorted(w.categories),
            promoted_markets_touched=promoted_touched,
            first_seen_in_window=None,  # composer carries string-fmt only
            is_fresh=bool(w.is_fresh),
            market_ids=[mid for mid in market_ids if mid],
        )

    # ── Markets ────────────────────────────────────────────────────
    # The composer surfaces promoted markets with category info. For
    # non-promoted markets we need to walk `report.sections` to pick
    # up per-market signal rows.
    markets: dict[str, MarketMetric] = {}
    # Per-market wallet set for unique_wallets count.
    per_market_wallets: dict[str, set[str]] = {}
    per_market_trades: dict[str, int] = Counter()
    per_market_volume: dict[str, float] = {}
    per_market_signals: dict[str, set[str]] = {}
    per_market_categories: dict[str, set[str]] = {}
    per_market_title: dict[str, str] = {}
    per_market_last_price: dict[str, float] = {}

    for section in report.sections:
        sig_id = section.signal_id
        sig_cat = section.category
        for row in section.rows:
            mid = str(row.get("market_id", "")).lower()
            if not mid:
                continue
            per_market_signals.setdefault(mid, set()).add(sig_id)
            per_market_categories.setdefault(mid, set()).add(sig_cat)
            if not per_market_title.get(mid):
                per_market_title[mid] = str(row.get("market_title", ""))
            # Volume / price derivation — prefer explicit fields.
            vol = row.get("volume_24h") or row.get("volume_window")
            if vol is not None:
                with contextlib.suppress(TypeError, ValueError):
                    per_market_volume[mid] = max(
                        per_market_volume.get(mid, 0.0),
                        float(vol),
                    )
            ltp = row.get("last_trade_price") or row.get("price")
            if ltp is not None:
                with contextlib.suppress(TypeError, ValueError):
                    per_market_last_price[mid] = float(ltp)
            # Contributor wallets.
            for tw in row.get("top_wallets", []) or []:
                addr = str(tw.get("address", "")).lower()
                if addr:
                    per_market_wallets.setdefault(mid, set()).add(addr)
            w_addr = str(row.get("wallet_address", "")).lower()
            if w_addr:
                per_market_wallets.setdefault(mid, set()).add(w_addr)

    # Raw-alerts pass — trade counts + extra wallet participation.
    for alert in report.raw_alerts:
        mid = str(alert.get("market_id", "")).lower()
        if not mid:
            continue
        per_market_trades[mid] += 1
        w_addr = str(alert.get("wallet", "")).lower()
        if w_addr:
            per_market_wallets.setdefault(mid, set()).add(w_addr)
        if not per_market_title.get(mid):
            per_market_title[mid] = str(alert.get("title", ""))

    # Compose MarketMetric per market.
    all_market_ids = (
        set(per_market_signals.keys())
        | set(per_market_trades.keys())
        | {p.market_id.lower() for p in promoted_markets}
    )
    for mid in all_market_ids:
        promoted = mid in promoted_mids
        # Promoted markets carry a canonical title + categories.
        title = per_market_title.get(mid, "")
        cats: set[str] = per_market_categories.get(mid, set())
        sig_hits: set[str] = per_market_signals.get(mid, set())
        for p in promoted_markets:
            if p.market_id.lower() == mid:
                title = title or p.market_title
                cats = cats | set(p.categories)
                for name in p.signal_names:
                    if name in name_to_id:
                        sig_hits.add(name_to_id[name])
                break
        markets[mid] = MarketMetric(
            condition_id=mid,
            title=title,
            event_slug="",
            volume_window=per_market_volume.get(mid, 0.0),
            last_trade_price=per_market_last_price.get(mid),
            signal_hits=sorted(sig_hits),
            categories=sorted(cats),
            unique_wallets=len(per_market_wallets.get(mid, set())),
            trade_count=per_market_trades.get(mid, 0),
            promoted=promoted,
        )

    # ── Counts ─────────────────────────────────────────────────────
    signal_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    for section in report.sections:
        n = len(section.rows)
        if n == 0:
            continue
        signal_counts[section.signal_id] += n
        category_counts[section.category] += n

    # Context-level stats derived from raw_alerts (raw_alerts is the
    # composer's materialised trade list for the window).
    total_trades = len(report.raw_alerts)
    total_notional = 0.0
    wallets_in_window: set[str] = set()
    for alert in report.raw_alerts:
        with contextlib.suppress(ValueError, ArithmeticError):
            total_notional += float(Decimal(str(alert.get("notional", "0"))))
        w_addr = str(alert.get("wallet", "")).lower()
        if w_addr:
            wallets_in_window.add(w_addr)

    # Deferred signal-id resolution for wallets whose signal_names
    # point at signals not in the registry (defensive against drift).
    for w_metric in wallets.values():
        for sig_id in list(w_metric.signals_fired):
            if sig_id not in id_to_category:
                # Keep the id; category_counts stays accurate either way.
                pass

    return DailyMetricsSnapshot(
        window_start=report.window_start,
        window_end=report.window_end,
        edition_id=report.edition_id,
        date=str(report.date),
        source_label=source_label or report.source_label,
        wallets=wallets,
        markets=markets,
        signal_counts=dict(signal_counts),
        category_counts=dict(category_counts),
        total_trades=total_trades,
        total_notional=total_notional,
        unique_wallets_in_window=len(wallets_in_window),
    )
