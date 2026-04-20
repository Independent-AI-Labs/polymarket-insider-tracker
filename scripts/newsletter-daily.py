#!/usr/bin/env python3
"""Phase N1 daily newsletter — alert-led Overnight Watchlist.

Per docs/SPEC-NEWSLETTERS-POLYMARKET § 4. Every section renders from
a real data source:

- `alert_daily_rollup` (once the detector pipeline writes rows)
- `data-api.polymarket.com/trades` (live Tier-2 source, always real)
- `funding_transfers` (once the funding tracer populates it)

No hand-seeded demo rows. When a section has no data, it says so and
the email still ships — that's the honesty rule from SPEC § 7.1 and
§ 9.

Usage:
    uv run python scripts/newsletter-daily.py --no-send
    uv run python scripts/newsletter-daily.py --dry-run
    uv run python scripts/newsletter-daily.py                 # real send
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from newsletter_common import deliver_via_himalaya  # noqa: E402

from polymarket_insider_tracker.config import get_settings  # noqa: E402
from polymarket_insider_tracker.storage.models import (  # noqa: E402
    AlertDailyRollupModel,
    FundingTransferModel,
)

LOG = logging.getLogger("newsletter-daily")

TEMPLATES_DIR = SCRIPT_DIR / "templates"
DAILY_TEMPLATE = TEMPLATES_DIR / "polymarket-daily.html"
CONFIG_PATH = SCRIPT_DIR / "report-config.yaml"
REPORTS_DIR = PROJECT_ROOT / "reports"

DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

# Minimum notional (in USDC-equivalent dollars) for a trade to count
# as "size-meaningful" for the daily. Retail micro-trades below this
# are what drowned the first iteration's headline — filtering at the
# data-api edge drops them before they reach ranking. $10K matches
# what M. (see docs/NEWSLETTER-REVIEW-RUBRIC.md) actually cares about.
DAILY_MIN_NOTIONAL = 10_000

# data-api caps historical offset at 3000 rows. Combined with
# filterAmount=10000, this covers ~24-36 h of size-meaningful flow.
MAX_HISTORICAL_OFFSET = 3000
PAGE_SIZE = 500

# Palette for the funding-origin bar.
FUNDING_COLORS = {
    "Binance": "#f0b90b",
    "Coinbase": "#0052ff",
    "Kraken": "#5741d9",
    "Other CEX": "#888",
    "On-chain": "#4c9",
    "Unknown": "#bbb",
}


# ── Row types ────────────────────────────────────────────────────────


@dataclass
class AlphaWallet:
    wallet_address: str
    market_title: str
    event_slug: str
    side: str
    notional: Decimal
    signals: str  # "" when no detector signal — hidden from the template

    @property
    def wallet_short(self) -> str:
        a = self.wallet_address
        return f"{a[:6]}…{a[-4:]}" if len(a) > 10 else a

    @property
    def notional_fmt(self) -> str:
        return f"{self.notional:,.0f}"


@dataclass
class TopMarket:
    market_id: str
    market_title: str
    event_slug: str
    alert_count: int  # when detector rollup empty, this is "trade count"
    unique_wallets: int
    notional: Decimal
    close_at: str = ""  # ISO date, gamma-api `endDate`; "" if unknown

    @property
    def notional_fmt(self) -> str:
        return f"{self.notional:,.0f}"

    @property
    def close_at_fmt(self) -> str:
        """Render as YYYY-MM-DD or a relative 'Xd' if ≤ 30 days out."""
        if not self.close_at:
            return "—"
        try:
            dt = datetime.fromisoformat(self.close_at.replace("Z", "+00:00"))
        except ValueError:
            return self.close_at[:10] if len(self.close_at) >= 10 else self.close_at
        now = datetime.now(UTC)
        delta = (dt - now).total_seconds() / 86400
        if 0 <= delta <= 30:
            return f"{dt:%Y-%m-%d} ({delta:.0f}d)"
        return f"{dt:%Y-%m-%d}"


@dataclass
class FundingOrigin:
    label: str
    pct: int
    color: str


@dataclass
class AsymmetricFlow:
    """A market whose 24h buy/sell split is lopsided enough to be a signal."""

    market_id: str
    market_title: str
    event_slug: str
    buy_count: int
    sell_count: int
    buy_notional: Decimal
    sell_notional: Decimal
    close_at: str = ""

    @property
    def dominant_side(self) -> str:
        return "BUY" if self.buy_notional >= self.sell_notional else "SELL"

    @property
    def net_notional(self) -> Decimal:
        return self.buy_notional - self.sell_notional

    @property
    def net_notional_fmt(self) -> str:
        return f"{self.net_notional:+,.0f}"

    @property
    def ratio_pct(self) -> int:
        total = self.buy_notional + self.sell_notional
        if total == 0:
            return 0
        dominant = max(self.buy_notional, self.sell_notional)
        return int((dominant / total) * 100)


@dataclass
class TimeCluster:
    """3+ distinct wallets entering the same market inside a 60 s window."""

    market_id: str
    market_title: str
    event_slug: str
    wallet_count: int
    time_span_s: int
    window_start: datetime
    combined_notional: Decimal

    @property
    def window_fmt(self) -> str:
        return f"{self.window_start:%Y-%m-%d %H:%M:%S}"

    @property
    def combined_notional_fmt(self) -> str:
        return f"{self.combined_notional:,.0f}"


@dataclass
class VolumeSpike:
    """Market whose 24h volume outruns its all-time daily baseline."""

    market_id: str
    market_title: str
    event_slug: str
    volume_24h: Decimal
    baseline_daily: Decimal
    days_active: float

    @property
    def multiple(self) -> float:
        if self.baseline_daily <= 0:
            return 0.0
        return float(self.volume_24h / self.baseline_daily)

    @property
    def volume_24h_fmt(self) -> str:
        return f"{self.volume_24h:,.0f}"

    @property
    def baseline_fmt(self) -> str:
        return f"{self.baseline_daily:,.0f}"


@dataclass
class PriceMover:
    """Market with the largest intra-window price swing."""

    market_id: str
    market_title: str
    event_slug: str
    first_price: Decimal
    last_price: Decimal
    trade_count: int
    close_at: str = ""

    @property
    def delta_bps(self) -> int:
        if self.first_price == 0:
            return 0
        return int(((self.last_price - self.first_price) / self.first_price) * 10_000)

    @property
    def delta_fmt(self) -> str:
        return f"{self.delta_bps:+,} bps"

    @property
    def price_arrow(self) -> str:
        return f"{self.first_price:.3f} → {self.last_price:.3f}"


@dataclass
class DailyContext:
    date: str
    window_end: str
    edition_id: str
    headline: str
    source_label: str  # "detector-rollup" | "data-api-live"
    alpha_wallets: list[AlphaWallet]
    top_markets: list[TopMarket]
    asymmetric_flows: list[AsymmetricFlow]
    time_clusters: list[TimeCluster]
    volume_spikes: list[VolumeSpike]
    price_movers: list[PriceMover]
    funding_origins: list[FundingOrigin]
    style: dict[str, str]
    footer: dict[str, str]
    raw_alerts: list[dict] = field(default_factory=list)

    def as_tera_payload(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "window_end": self.window_end,
            "edition_id": self.edition_id,
            "headline": self.headline,
            "source_label": self.source_label,
            "alpha_wallets": [
                {
                    "wallet_address": w.wallet_address,
                    "wallet_short": w.wallet_short,
                    "market_title": w.market_title,
                    "event_slug": w.event_slug,
                    "side": w.side,
                    "notional_fmt": w.notional_fmt,
                    "signals": w.signals,
                }
                for w in self.alpha_wallets
            ],
            "top_markets": [
                {
                    "market_title": m.market_title,
                    "event_slug": m.event_slug,
                    "alert_count": m.alert_count,
                    "unique_wallets": m.unique_wallets,
                    "notional_fmt": m.notional_fmt,
                    "close_at_fmt": m.close_at_fmt,
                }
                for m in self.top_markets
            ],
            "asymmetric_flows": [
                {
                    "market_title": a.market_title,
                    "event_slug": a.event_slug,
                    "buy_count": a.buy_count,
                    "sell_count": a.sell_count,
                    "dominant_side": a.dominant_side,
                    "net_notional_fmt": a.net_notional_fmt,
                    "ratio_pct": a.ratio_pct,
                    "close_at": a.close_at,
                }
                for a in self.asymmetric_flows
            ],
            "time_clusters": [
                {
                    "market_title": c.market_title,
                    "event_slug": c.event_slug,
                    "wallet_count": c.wallet_count,
                    "time_span_s": c.time_span_s,
                    "window_fmt": c.window_fmt,
                    "combined_notional_fmt": c.combined_notional_fmt,
                }
                for c in self.time_clusters
            ],
            "volume_spikes": [
                {
                    "market_title": v.market_title,
                    "event_slug": v.event_slug,
                    "volume_24h_fmt": v.volume_24h_fmt,
                    "baseline_fmt": v.baseline_fmt,
                    "multiple": f"{v.multiple:.1f}",
                    "days_active": f"{v.days_active:.0f}",
                }
                for v in self.volume_spikes
            ],
            "price_movers": [
                {
                    "market_title": p.market_title,
                    "event_slug": p.event_slug,
                    "price_arrow": p.price_arrow,
                    "delta_fmt": p.delta_fmt,
                    "delta_bps": p.delta_bps,
                    "trade_count": p.trade_count,
                    "close_at": p.close_at,
                }
                for p in self.price_movers
            ],
            "funding_origins": [
                {"label": f.label, "pct": f.pct, "color": f.color}
                for f in self.funding_origins
            ],
            "style": self.style,
            "footer": self.footer,
        }


# ── Real data sources ────────────────────────────────────────────────


async def _fetch_rollup(
    target_date: date,
) -> list[AlertDailyRollupModel]:
    settings = get_settings()
    engine = create_async_engine(settings.database.url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with factory() as session:
            return (
                await session.execute(
                    select(AlertDailyRollupModel).where(
                        AlertDailyRollupModel.day == target_date
                    )
                )
            ).scalars().all()
    finally:
        await engine.dispose()


def _fetch_live_trades(
    *,
    window_hours: float = 24,
    min_notional: float = DAILY_MIN_NOTIONAL,
) -> list[dict[str, Any]]:
    """Pull size-meaningful trades from data-api over a ~24 h window.

    Strategy: page through `?filterAmount={min_notional}&offset=0..3000`,
    stopping when either the data-api offset ceiling is hit or the
    oldest fetched trade is ≥ `window_hours` ago. `filterAmount` is a
    server-side filter on USDC notional — it drops retail micro-trades
    that otherwise drown a daily window's signal.

    Returns a list of raw trade dicts, newest-first, deduped on
    `transactionHash`.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
    cutoff_ts = int(cutoff.timestamp())
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    with httpx.Client(timeout=30.0) as client:
        for offset in range(0, MAX_HISTORICAL_OFFSET + 1, PAGE_SIZE):
            resp = client.get(
                f"{DATA_API}/trades",
                params={
                    "limit": PAGE_SIZE,
                    "offset": offset,
                    "filterAmount": min_notional,
                },
            )
            if resp.status_code != 200:
                LOG.warning(
                    "data-api offset=%d returned HTTP %d; stopping pagination",
                    offset,
                    resp.status_code,
                )
                break
            rows = resp.json()
            if not isinstance(rows, list) or not rows:
                break
            oldest_in_page: int | None = None
            for r in rows:
                tx = str(r.get("transactionHash", ""))
                if tx and tx in seen:
                    continue
                seen.add(tx)
                out.append(r)
                ts = int(r.get("timestamp", 0))
                if oldest_in_page is None or ts < oldest_in_page:
                    oldest_in_page = ts
            if oldest_in_page is not None and oldest_in_page <= cutoff_ts:
                break
    # Final cutoff trim — in case the last page straddled the window.
    return [r for r in out if int(r.get("timestamp", 0)) >= cutoff_ts]


def _fetch_market_metadata(
    market_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Bulk-fetch gamma-api for volume + close-date + start-date.

    Returns mapping of lowercased conditionId → full market dict.
    """
    if not market_ids:
        return {}
    out: dict[str, dict[str, Any]] = {}
    with httpx.Client(timeout=30.0) as client:
        for i in range(0, len(market_ids), 50):
            chunk = market_ids[i : i + 50]
            params = [("condition_ids", cid) for cid in chunk]
            params.append(("limit", str(len(chunk))))
            resp = client.get(f"{GAMMA_API}/markets", params=params)
            if resp.status_code != 200:
                LOG.warning("gamma condition_ids chunk failed: %d", resp.status_code)
                continue
            for m in resp.json():
                cid = str(m.get("conditionId", "")).lower()
                if cid:
                    out[cid] = m
    return out


def _fetch_market_close_dates(
    market_ids: list[str],
) -> dict[str, str]:
    """Legacy shim — callers that only want close dates."""
    meta = _fetch_market_metadata(market_ids)
    return {
        cid: str(m.get("endDate", "")) or str(m.get("endDateIso", ""))
        for cid, m in meta.items()
    }


# ── Signal computations (single-pass over the 24 h trade list) ──────


def compute_asymmetric_flows(
    trades: list[dict[str, Any]],
    close_dates: dict[str, str],
    *,
    min_trades: int = 5,
    min_dominance: float = 0.70,
    top_n: int = 3,
) -> list[AsymmetricFlow]:
    """Flag markets where the buy/sell split is lopsided.

    A market makes the cut when ≥ `min_trades` trades clear the window,
    the dominant side accounts for ≥ `min_dominance` share of combined
    notional, and the ranking key is dominant-side notional so we
    surface the biggest one-sided markets, not the most lopsided tiny
    ones.
    """
    by_market: dict[str, dict[str, Any]] = {}
    for t in trades:
        mid = str(t.get("conditionId", ""))
        if not mid:
            continue
        entry = by_market.setdefault(
            mid,
            {
                "market_id": mid,
                "market_title": str(t.get("title", "")),
                "event_slug": str(t.get("eventSlug", "")),
                "buy_count": 0,
                "sell_count": 0,
                "buy_notional": Decimal("0"),
                "sell_notional": Decimal("0"),
            },
        )
        size = Decimal(str(t.get("size", 0)))
        price = Decimal(str(t.get("price", 0)))
        notional = size * price
        if str(t.get("side", "")).upper() == "BUY":
            entry["buy_count"] += 1
            entry["buy_notional"] += notional
        else:
            entry["sell_count"] += 1
            entry["sell_notional"] += notional

    rows: list[AsymmetricFlow] = []
    for mid, e in by_market.items():
        total_trades = e["buy_count"] + e["sell_count"]
        total_notional = e["buy_notional"] + e["sell_notional"]
        if total_trades < min_trades or total_notional <= 0:
            continue
        dominant = max(e["buy_notional"], e["sell_notional"])
        if (dominant / total_notional) < Decimal(str(min_dominance)):
            continue
        rows.append(
            AsymmetricFlow(
                market_id=mid,
                market_title=e["market_title"],
                event_slug=e["event_slug"],
                buy_count=e["buy_count"],
                sell_count=e["sell_count"],
                buy_notional=e["buy_notional"].quantize(Decimal("1")),
                sell_notional=e["sell_notional"].quantize(Decimal("1")),
                close_at=close_dates.get(mid.lower(), ""),
            )
        )
    rows.sort(key=lambda r: max(r.buy_notional, r.sell_notional), reverse=True)
    return rows[:top_n]


def compute_time_clusters(
    trades: list[dict[str, Any]],
    *,
    window_seconds: int = 60,
    min_wallets: int = 3,
    top_n: int = 3,
) -> list[TimeCluster]:
    """Find co-timed entries: ≥ N distinct wallets into the same market
    within a rolling `window_seconds` window.
    """
    # Group trades by market, sorted by timestamp ascending.
    by_market: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        mid = str(t.get("conditionId", ""))
        if mid:
            by_market.setdefault(mid, []).append(t)
    clusters: list[TimeCluster] = []
    for mid, market_trades in by_market.items():
        market_trades.sort(key=lambda r: int(r.get("timestamp", 0)))
        # Rolling window: for each trade, find all trades within
        # window_seconds AFTER it; if distinct wallets ≥ min_wallets,
        # record a cluster.
        seen_windows: set[tuple[int, str]] = set()
        for i, anchor in enumerate(market_trades):
            anchor_ts = int(anchor.get("timestamp", 0))
            window_end = anchor_ts + window_seconds
            wallets: set[str] = set()
            notional = Decimal("0")
            last_ts = anchor_ts
            for j in range(i, len(market_trades)):
                tj = market_trades[j]
                ts = int(tj.get("timestamp", 0))
                if ts > window_end:
                    break
                wallets.add(str(tj.get("proxyWallet", "")).lower())
                notional += Decimal(str(tj.get("size", 0))) * Decimal(
                    str(tj.get("price", 0))
                )
                last_ts = ts
            if len(wallets) < min_wallets:
                continue
            # De-dupe overlapping anchors: keep the earliest that still
            # captures this wallet set.
            key = (anchor_ts // window_seconds, mid)
            if key in seen_windows:
                continue
            seen_windows.add(key)
            clusters.append(
                TimeCluster(
                    market_id=mid,
                    market_title=str(anchor.get("title", "")),
                    event_slug=str(anchor.get("eventSlug", "")),
                    wallet_count=len(wallets),
                    time_span_s=last_ts - anchor_ts,
                    window_start=datetime.fromtimestamp(anchor_ts, UTC),
                    combined_notional=notional.quantize(Decimal("1")),
                )
            )
    clusters.sort(
        key=lambda c: (c.wallet_count, c.combined_notional), reverse=True
    )
    return clusters[:top_n]


def compute_volume_spikes(
    market_meta: dict[str, dict[str, Any]],
    *,
    min_multiple: float = 3.0,
    min_days_active: float = 3.0,
    top_n: int = 3,
) -> list[VolumeSpike]:
    """Markets whose 24 h volume outruns their all-time daily average.

    Baseline = `volumeNum / days_active`. Filters out markets that are
    < `min_days_active` old, because a 2-day-old market's "baseline"
    is not stable enough to compare against. Ranked by multiple.
    """
    rows: list[VolumeSpike] = []
    now = datetime.now(UTC)
    for cid, m in market_meta.items():
        vol_24h = Decimal(str(m.get("volume24hr", 0) or 0))
        vol_total = Decimal(str(m.get("volumeNum", 0) or 0))
        start_raw = str(m.get("startDate", "") or m.get("startDateIso", "") or "")
        if not start_raw:
            continue
        try:
            start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        days_active = max((now - start_dt).total_seconds() / 86400, 0.1)
        if days_active < min_days_active or vol_total <= 0:
            continue
        baseline = vol_total / Decimal(str(days_active))
        if baseline <= 0:
            continue
        multiple = float(vol_24h / baseline)
        if multiple < min_multiple:
            continue
        rows.append(
            VolumeSpike(
                market_id=cid,
                market_title=str(m.get("question", "")),
                event_slug=str(m.get("slug", "")),
                volume_24h=vol_24h.quantize(Decimal("1")),
                baseline_daily=baseline.quantize(Decimal("1")),
                days_active=days_active,
            )
        )
    rows.sort(key=lambda r: r.multiple, reverse=True)
    return rows[:top_n]


def compute_price_movers(
    trades: list[dict[str, Any]],
    close_dates: dict[str, str],
    *,
    min_trades: int = 5,
    min_abs_bps: int = 500,
    top_n: int = 3,
) -> list[PriceMover]:
    """Markets where the intra-window price moved > `min_abs_bps`.

    Uses first and last observed prices within the trade list — NOT
    bid/ask — so this is a trade-weighted price delta, not a book
    delta. Noisy with few trades so we require ≥ min_trades.
    """
    by_market: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        mid = str(t.get("conditionId", ""))
        if mid:
            by_market.setdefault(mid, []).append(t)
    rows: list[PriceMover] = []
    for mid, market_trades in by_market.items():
        if len(market_trades) < min_trades:
            continue
        market_trades.sort(key=lambda r: int(r.get("timestamp", 0)))
        first = Decimal(str(market_trades[0].get("price", 0)))
        last = Decimal(str(market_trades[-1].get("price", 0)))
        if first == 0:
            continue
        bps = int(((last - first) / first) * 10_000)
        if abs(bps) < min_abs_bps:
            continue
        rows.append(
            PriceMover(
                market_id=mid,
                market_title=str(market_trades[0].get("title", "")),
                event_slug=str(market_trades[0].get("eventSlug", "")),
                first_price=first,
                last_price=last,
                trade_count=len(market_trades),
                close_at=close_dates.get(mid.lower(), ""),
            )
        )
    rows.sort(key=lambda r: abs(r.delta_bps), reverse=True)
    return rows[:top_n]


async def _fetch_funding_origins_real(
    wallets: set[str],
) -> list[FundingOrigin]:
    """Query funding_transfers for first-funding origin of each wallet.

    Returns `[]` if there are no rows — which disables the funding bar
    section in the template rather than emitting invented percentages.
    """
    if not wallets:
        return []
    settings = get_settings()
    engine = create_async_engine(settings.database.url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with factory() as session:
            rows = (
                await session.execute(
                    select(FundingTransferModel).where(
                        FundingTransferModel.to_address.in_([w.lower() for w in wallets])
                    )
                )
            ).scalars().all()
    finally:
        await engine.dispose()
    if not rows:
        return []
    # Bucketing logic lives in profiler/entities.py; we keep this
    # section empty until that path is wired end-to-end so we never
    # emit a fabricated breakdown.
    return []


# ── Context builders ─────────────────────────────────────────────────


async def build_context(
    target_date: date, cfg: dict[str, Any]
) -> DailyContext:
    """Primary entrypoint. Tries rollup first, falls back to live data-api."""
    rollup_rows = await _fetch_rollup(target_date)
    if rollup_rows:
        return await _context_from_rollup(target_date, cfg, rollup_rows)
    LOG.warning(
        "alert_daily_rollup empty for %s — rendering from live data-api "
        "feed; alerts section will show trade activity, not detector "
        "signals",
        target_date,
    )
    return await _context_from_live(target_date, cfg)


async def _context_from_rollup(
    target_date: date,
    cfg: dict[str, Any],
    rollup_rows: list[AlertDailyRollupModel],
) -> DailyContext:
    """Render from the detector pipeline's rollup table (real signals)."""
    # This path is built out when the detector pipeline starts
    # populating `alert_daily_rollup`. Until then we never reach this
    # branch in production. Leaving it unimplemented keeps the failure
    # loud and prevents a silent "mock from empty query" anti-pattern.
    raise NotImplementedError(
        "rollup-based daily not wired yet — "
        "run the detector pipeline and populate alert_daily_rollup, "
        "or retire this branch once data-api-only is accepted"
    )


async def _context_from_live(
    target_date: date, cfg: dict[str, Any]
) -> DailyContext:
    """Render from a paginated 24 h data-api window. Every row is real."""
    trades = _fetch_live_trades(window_hours=24, min_notional=DAILY_MIN_NOTIONAL)
    LOG.info(
        "live source: %d trades ≥ $%d over past 24 h",
        len(trades),
        DAILY_MIN_NOTIONAL,
    )

    # Top wallet+market pairs by notional — real addresses, real slugs.
    seen: set[tuple[str, str]] = set()
    alpha: list[AlphaWallet] = []
    for t in sorted(
        trades,
        key=lambda r: float(r.get("size", 0)) * float(r.get("price", 0)),
        reverse=True,
    ):
        wallet = str(t.get("proxyWallet", "")).lower()
        market_id = str(t.get("conditionId", ""))
        if not wallet or (wallet, market_id) in seen:
            continue
        seen.add((wallet, market_id))
        notional = Decimal(str(t.get("size", 0))) * Decimal(str(t.get("price", 0)))
        alpha.append(
            AlphaWallet(
                wallet_address=wallet,
                market_title=str(t.get("title", "(unknown market)")),
                event_slug=str(t.get("eventSlug", "")),
                side=str(t.get("side", "")),
                notional=notional.quantize(Decimal("1")),
                signals="",  # no detector output — template hides the column
            )
        )
        if len(alpha) >= 5:
            break

    # Top markets by summed notional across the live window.
    by_market: dict[str, dict[str, Any]] = {}
    for t in trades:
        mid = str(t.get("conditionId", ""))
        if not mid:
            continue
        entry = by_market.setdefault(
            mid,
            {
                "market_id": mid,
                "market_title": str(t.get("title", "(unknown market)")),
                "event_slug": str(t.get("eventSlug", "")),
                "trade_count": 0,
                "wallets": set(),
                "notional": Decimal("0"),
            },
        )
        entry["trade_count"] += 1
        entry["wallets"].add(str(t.get("proxyWallet", "")).lower())
        entry["notional"] += Decimal(str(t.get("size", 0))) * Decimal(
            str(t.get("price", 0))
        )
    # Bulk-fetch metadata for the top 30 markets by 24h notional so
    # the signal computations can access volume / start-date / end-date
    # without re-fetching per section.
    ranked_by_notional = sorted(
        by_market.values(), key=lambda e: e["notional"], reverse=True
    )
    meta_ids = [e["market_id"] for e in ranked_by_notional[:30]]
    market_meta = _fetch_market_metadata(meta_ids)

    # Filter out markets whose endDate has already passed. A market
    # that closed 2 days ago is not a candidate for "Top markets" or
    # any signal section — that was the Hezbollah bug.
    now = datetime.now(UTC)

    def _is_future_market(cid: str) -> bool:
        m = market_meta.get(cid.lower())
        if not m:
            return True  # missing meta — don't drop silently
        if bool(m.get("closed")) is True:
            return False
        end_raw = str(m.get("endDate", "") or m.get("endDateIso", "") or "")
        if not end_raw:
            return True
        try:
            end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
        except ValueError:
            return True
        return end_dt > now

    ranked_future = [e for e in ranked_by_notional if _is_future_market(e["market_id"])]

    # Also prune the alpha-wallets list to markets still open.
    alpha = [w for w in alpha if _is_future_market(
        next(
            (e["market_id"] for e in ranked_by_notional
             if e["event_slug"] == w.event_slug),
            "",
        )
    )]

    close_dates = {
        cid: str(m.get("endDate", "") or m.get("endDateIso", "") or "")
        for cid, m in market_meta.items()
    }

    markets_ranked = ranked_future[:3]
    markets = [
        TopMarket(
            market_id=e["market_id"],
            market_title=e["market_title"],
            event_slug=e["event_slug"],
            alert_count=int(e["trade_count"]),
            unique_wallets=len(e["wallets"]),
            notional=e["notional"].quantize(Decimal("1")),
            close_at=close_dates.get(e["market_id"].lower(), ""),
        )
        for e in markets_ranked
    ]

    # Prune trades list to only future markets for signal computations.
    live_market_ids = {e["market_id"].lower() for e in ranked_future}
    future_trades = [
        t for t in trades
        if str(t.get("conditionId", "")).lower() in live_market_ids
    ]

    # ── Four derived signal sections ────────────────────────────
    asymmetric_flows = compute_asymmetric_flows(future_trades, close_dates)
    time_clusters = compute_time_clusters(future_trades)
    volume_spikes = compute_volume_spikes(
        {cid: m for cid, m in market_meta.items() if _is_future_market(cid)}
    )
    price_movers = compute_price_movers(future_trades, close_dates)
    LOG.info(
        "signals: asymmetric=%d clusters=%d spikes=%d movers=%d",
        len(asymmetric_flows),
        len(time_clusters),
        len(volume_spikes),
        len(price_movers),
    )

    # Funding origin only rendered if funding_transfers is populated.
    funding = await _fetch_funding_origins_real({a.wallet_address for a in alpha})

    if trades:
        first_ts = datetime.fromtimestamp(min(int(t["timestamp"]) for t in trades), UTC)
        last_ts = datetime.fromtimestamp(max(int(t["timestamp"]) for t in trades), UTC)
        span_h = (last_ts - first_ts).total_seconds() / 3600
        window_human = (
            f"{first_ts:%Y-%m-%d %H:%M} → {last_ts:%Y-%m-%d %H:%M} UTC "
            f"({span_h:.1f} h)"
        )
    else:
        first_ts = last_ts = datetime.now(UTC)
        span_h = 0.0
        window_human = "(no trades cleared the $10K filter in this window)"

    if markets:
        top_title = markets[0].market_title
        headline = (
            f"{len(trades)} trades ≥ ${DAILY_MIN_NOTIONAL:,} observed across "
            f"{window_human}. Heaviest flow: <em>{top_title}</em> — "
            f"${markets[0].notional:,.0f} from {markets[0].unique_wallets} "
            f"wallets. Source: Polymarket data-api live feed; detector "
            f"rollup not yet populated, so ranking is by raw notional, "
            f"not by signal score."
        )
    else:
        headline = (
            f"No trades ≥ ${DAILY_MIN_NOTIONAL:,} in the last 24 h window "
            "— unusually quiet."
        )

    style = cfg["email"]["style"]
    footer = {
        "legal_name": "Independent AI Labs",
        "postal_address": "ami-reports@independentailabs.com",
    }

    raw_alerts = [
        {
            "ts": datetime.fromtimestamp(int(t["timestamp"]), UTC).isoformat(),
            "wallet": t.get("proxyWallet", ""),
            "market_id": t.get("conditionId", ""),
            "market_slug": t.get("eventSlug", ""),
            "title": t.get("title", ""),
            "side": t.get("side", ""),
            "size": t.get("size", ""),
            "price": t.get("price", ""),
            "notional": str(
                Decimal(str(t.get("size", 0))) * Decimal(str(t.get("price", 0)))
            ),
            "tx_hash": t.get("transactionHash", ""),
        }
        for t in trades
    ]

    return DailyContext(
        date=target_date.isoformat(),
        window_end=f"{datetime.now(UTC):%Y-%m-%d %H:%M}",
        edition_id=f"daily-{target_date.isoformat()}",
        headline=headline,
        source_label="data-api-live",
        alpha_wallets=alpha,
        top_markets=markets,
        asymmetric_flows=asymmetric_flows,
        time_clusters=time_clusters,
        volume_spikes=volume_spikes,
        price_movers=price_movers,
        funding_origins=funding,
        style={str(k): str(v) for k, v in style.items()},
        footer=footer,
        raw_alerts=raw_alerts,
    )


# ── CSV attachment ──────────────────────────────────────────────────


def write_alerts_csv(ctx: DailyContext, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"alerts-{ctx.date}.csv"
    if not ctx.raw_alerts:
        path.write_text("# no trades in window\n", encoding="utf-8")
        return path
    fieldnames = list(ctx.raw_alerts[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(ctx.raw_alerts)
    return path


# ── Delivery ────────────────────────────────────────────────────────


def build_rows(
    ctx: DailyContext, cfg: dict[str, Any], targets_filter: str | None
) -> list[dict[str, Any]]:
    targets = cfg["delivery"]["targets"]
    if targets_filter:
        wanted = {t.strip() for t in targets_filter.split(",")}
        targets = [t for t in targets if t["name"] in wanted]
    else:
        targets = [t for t in targets if t.get("enabled", True)]

    subject = f"[AMI] Polymarket Watchlist — {ctx.date}"

    rows = []
    for t in targets:
        rows.append(
            {
                "email": t["email"],
                "name": t.get("name", t["email"]),
                "subject": subject,
                "reason": "you're on the canary list for Polymarket newsletter iteration",
                "unsubscribe_url": "https://example.invalid/unsubscribe?token=canary-placeholder",
                "daily": ctx.as_tera_payload(),
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase N1 daily newsletter")
    parser.add_argument(
        "--date",
        default=datetime.now(UTC).date().isoformat(),
        help="Edition date YYYY-MM-DD (default: today UTC)",
    )
    parser.add_argument("--no-send", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--targets", default=None, help="comma-separated target names")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    with CONFIG_PATH.open() as fh:
        cfg = yaml.safe_load(fh)

    target_date = date.fromisoformat(args.date)
    try:
        ctx = asyncio.run(build_context(target_date, cfg))
    except NotImplementedError as exc:
        LOG.error("%s", exc)
        return 2

    attachments: list[Path] = []
    csv_path = write_alerts_csv(ctx, REPORTS_DIR)
    LOG.info("wrote %s", csv_path)
    attachments.append(csv_path)
    pdf_path = REPORTS_DIR / f"market-snapshot-{ctx.date}.pdf"
    if pdf_path.exists():
        attachments.append(pdf_path)
        LOG.info("attaching existing snapshot %s", pdf_path)

    if args.no_send:
        LOG.info("--no-send: rendered context + CSV only")
        LOG.info("headline: %s", ctx.headline)
        return 0

    rows = build_rows(ctx, cfg, args.targets)
    rc = deliver_via_himalaya(
        rows,
        template_path=DAILY_TEMPLATE,
        subject_template="{{ subject }}",
        account=cfg["delivery"]["account"],
        rate=cfg["delivery"].get("rate", "2/min"),
        attachments=attachments,
        dry_run=args.dry_run,
    )
    return rc


if __name__ == "__main__":
    sys.exit(main())
