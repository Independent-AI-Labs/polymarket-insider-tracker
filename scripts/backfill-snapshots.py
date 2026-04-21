#!/usr/bin/env python3
"""Historical backloader — populate MetricsStore from archival trades.

Phase 3 empirical pruning wants ~100 days of `DailyMetricsSnapshot`s
to compute pairwise signal correlations + silhouette scores over.
Running the live daily job for a week produces seven snapshots;
replaying it against historical data produces a hundred in one go.

Strategy (see docs/BACKFILL-CAVEATS.md for full context):

1. Enumerate every market whose lifespan overlaps [start, end] via
   gamma-api `/markets` paginated by `end_date_min` / `end_date_max`.
2. For each market, page data-api `/trades?market=<cid>` backwards
   until we cross `start` or hit the 3000-offset wall. Persist raw
   JSONL per market so crash-resume is free.
3. Bucket trades by UTC calendar day.
4. For each day D in the range: build a `SignalContext` from D's
   bucket, call `compose()`, project to `DailyMetricsSnapshot`,
   write via `MetricsStore`.

Signals that depend on live gamma fields (e.g. volume-velocity reads
today's `volume24hr`) are skipped in replay mode by default — the
`--skip-signal` flag and the REPLAY_SKIP_SIGNALS constant control
which ones. Everything else runs verbatim; gates that use
`datetime.now()` degrade permissively (markets that ended between D
and today fail the close-gate in replay but this shrinks the signal
set rather than producing false hits).

Usage:

    uv run python scripts/backfill-snapshots.py \\
        --start 2026-01-01 --end 2026-04-21

    uv run python scripts/backfill-snapshots.py \\
        --start 2026-04-15 --end 2026-04-20 --limit-markets 50

Output: one `DailyMetricsSnapshot` per UTC day under
`<metrics-root>/snapshots/YYYY/YYYY-MM-DD.json`.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Any

import httpx

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from polymarket_insider_tracker.detector.composer import compose  # noqa: E402
from polymarket_insider_tracker.detector.signals import SignalContext  # noqa: E402
from polymarket_insider_tracker.ingestor.data_api import (  # noqa: E402
    HistoricalTruncation,
    iter_trades_historical,
)
from polymarket_insider_tracker.metrics import (  # noqa: E402
    MetricsStore,
    default_root,
    snapshot_from_report,
)

LOG = logging.getLogger("backfill-snapshots")

GAMMA_API = "https://gamma-api.polymarket.com"
GAMMA_PAGE_SIZE = 500
GAMMA_MAX_PAGES = 200  # 500 × 200 = 100k markets — hard ceiling

# Signals known to reference "today" instead of the replayed window.
# 03-A volume-velocity reads gamma-api's `volume24hr` which is a
# live-state field — replaying it against a historical day would
# produce hits based on today's activity, not D's. Skip by default.
REPLAY_SKIP_SIGNALS: set[str] = {
    "03-A-volume-velocity",
}

DEFAULT_CACHE_ROOT = Path.home() / ".cache" / "polymarket-insider-tracker" / "backfill"

# Per-market fetch fan-out. data-api has no documented rate limit and
# the endpoint is Cloudflare-cached, so 16 concurrent worker threads
# landed fine in dev probes. Tune via --fetch-workers if the origin
# starts pushing back.
DEFAULT_FETCH_WORKERS = 16

# Markets with volumeNum == 0 provably have no trades (Polymarket's
# own metric), so we skip the per-market fetch for them. The `--min-volume`
# flag tightens this further — e.g. `--min-volume 100` drops markets
# whose all-time volume is below $100.
DEFAULT_MIN_VOLUME = 1.0


# ── Market enumeration ──────────────────────────────────────────────


def enumerate_markets(
    *,
    start: date,
    end: date,
    cache_path: Path,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """Fetch every market whose endDate sits in [start, end] inclusive.

    Results are cached to `cache_path` as JSON so re-runs skip the
    gamma walk entirely. The enumeration uses `closed=true&end_date_min=…`
    first to catch resolved markets, then a second pass with
    `closed=false&end_date_min=…&end_date_max=…` to pick up markets
    that were still live at `end`.
    """
    if cache_path.exists() and not force_refresh:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        LOG.info("market enum: loaded %d from cache %s", len(cached), cache_path)
        return cached

    by_cid: dict[str, dict[str, Any]] = {}
    start_iso = start.isoformat()
    end_iso = end.isoformat()

    for closed_flag in (True, False):
        page = 0
        while page < GAMMA_MAX_PAGES:
            params: dict[str, Any] = {
                "limit": GAMMA_PAGE_SIZE,
                "offset": page * GAMMA_PAGE_SIZE,
                "closed": "true" if closed_flag else "false",
                "end_date_min": start_iso,
                # end_date_max only filters for closed markets; live
                # markets with endDate past `end` are kept (they were
                # open during the backfill window).
                "order": "endDate",
                "ascending": "true",
            }
            if closed_flag:
                params["end_date_max"] = end_iso
            try:
                with httpx.Client(timeout=60.0) as client:
                    resp = client.get(f"{GAMMA_API}/markets", params=params)
            except httpx.HTTPError as exc:
                LOG.warning(
                    "gamma enum closed=%s page=%d failed: %s",
                    closed_flag, page, exc,
                )
                break
            if resp.status_code != 200:
                LOG.warning(
                    "gamma enum closed=%s page=%d http=%d",
                    closed_flag, page, resp.status_code,
                )
                break
            rows = resp.json()
            if not isinstance(rows, list) or not rows:
                break
            for m in rows:
                cid = str(m.get("conditionId", "") or "").lower()
                if not cid:
                    continue
                # For the closed=false pass we still need to cut off
                # markets that started AFTER `end` — those can't have
                # contributed trades in our window.
                start_raw = str(
                    m.get("startDate", "") or m.get("startDateIso", "")
                )
                if start_raw:
                    try:
                        mstart = datetime.fromisoformat(
                            start_raw.replace("Z", "+00:00")
                        ).date()
                    except ValueError:
                        mstart = None
                    if mstart and mstart > end:
                        continue
                by_cid[cid] = {
                    "condition_id": cid,
                    "slug": str(m.get("slug", "") or ""),
                    "question": str(m.get("question", "") or ""),
                    "startDate": start_raw,
                    "endDate": str(
                        m.get("endDate", "") or m.get("endDateIso", "")
                    ),
                    "closed": bool(m.get("closed")),
                    "volumeNum": float(m.get("volumeNum", 0) or 0),
                }
            if len(rows) < GAMMA_PAGE_SIZE:
                break
            page += 1

    markets = sorted(by_cid.values(), key=lambda m: m["volumeNum"], reverse=True)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(markets, indent=2), encoding="utf-8")
    LOG.info(
        "market enum: %d unique markets cached to %s",
        len(markets), cache_path,
    )
    return markets


# ── Per-market trade fetch (with on-disk cache) ────────────────────


def fetch_market_trades(
    *,
    market_id: str,
    start: datetime,
    end: datetime,
    cache_dir: Path,
    client: httpx.Client | None = None,
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    """Return `(rows, truncated)` for one market.

    On-disk layout: `<cache_dir>/<market_id>.jsonl`. One line per row.
    A trailing `.meta.json` sidecar records the window + truncation
    bit so re-runs don't have to probe again.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    trades_path = cache_dir / f"{market_id}.jsonl"
    meta_path = cache_dir / f"{market_id}.meta.json"

    if trades_path.exists() and meta_path.exists() and not force_refresh:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if (
            meta.get("start") == start.isoformat()
            and meta.get("end") == end.isoformat()
        ):
            rows = [
                json.loads(line)
                for line in trades_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            return rows, bool(meta.get("truncated", False))

    truncated = False
    rows: list[dict[str, Any]] = []
    try:
        for row in iter_trades_historical(
            market_id,
            start=start,
            end=end,
            client=client,
        ):
            rows.append(row)
    except HistoricalTruncation:
        truncated = True

    # Persist atomically — write into a .tmp then rename.
    tmp_trades = trades_path.with_suffix(".jsonl.tmp")
    with tmp_trades.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":")))
            fh.write("\n")
    tmp_trades.replace(trades_path)

    meta = {
        "market_id": market_id,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "row_count": len(rows),
        "truncated": truncated,
        "fetched_at": datetime.now(UTC).isoformat(),
    }
    tmp_meta = meta_path.with_suffix(".json.tmp")
    tmp_meta.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    tmp_meta.replace(meta_path)

    return rows, truncated


# ── Replay loop ─────────────────────────────────────────────────────


def _filter_registry(skip_ids: set[str]) -> list:
    """Remove signals whose ids are in `skip_ids` from the composer's
    REGISTRY (in-place). Returns the original list so callers can
    restore it. Mutating REGISTRY is the minimum-intrusion way to
    filter — composer reads from this module-level list directly.
    """
    from polymarket_insider_tracker.detector.signals import registry as reg_mod

    original = list(reg_mod.REGISTRY)
    reg_mod.REGISTRY[:] = [s for s in reg_mod.REGISTRY if s.id not in skip_ids]
    return original


def _restore_registry(original: list) -> None:
    from polymarket_insider_tracker.detector.signals import registry as reg_mod

    reg_mod.REGISTRY[:] = original


def replay_day(
    *,
    day: date,
    trades_by_day: dict[date, list[dict[str, Any]]],
    market_meta: dict[str, dict[str, Any]],
    store: MetricsStore,
    source_label: str,
) -> tuple[int, int]:
    """Build a context, compose a report, persist the snapshot.

    Returns `(n_trades, n_wallets)` for progress logging.
    """
    day_trades = trades_by_day.get(day, [])
    window_start = datetime.combine(day, dtime.min, tzinfo=UTC)
    window_end = window_start + timedelta(days=1)

    context = SignalContext(
        trades=day_trades,
        market_meta=market_meta,
        window_start=window_start,
        window_end=window_end,
        edition_date=day.isoformat(),
    )
    report = compose(context, source_label=source_label)
    # Overwrite edition_id so snapshot_from_report marks it as a
    # backfill rather than a normal daily run.
    report.edition_id = f"backfill-{day.isoformat()}"
    report.date = day.isoformat()

    snapshot = snapshot_from_report(report, source_label=source_label)
    store.write_snapshot(snapshot)
    return len(day_trades), snapshot.unique_wallets_in_window


# ── CLI ─────────────────────────────────────────────────────────────


def _daterange(start: date, end: date) -> list[date]:
    out: list[date] = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay historical trades into DailyMetricsSnapshots"
    )
    parser.add_argument("--start", required=True, help="YYYY-MM-DD inclusive")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD inclusive")
    parser.add_argument(
        "--markets-filter",
        default=None,
        help="JSON list of condition_ids, or a path to a JSON file; "
             "restricts processing to these markets.",
    )
    parser.add_argument(
        "--trade-cache-root",
        default=str(DEFAULT_CACHE_ROOT),
        help="Directory for per-market JSONL caches + gamma market enum.",
    )
    parser.add_argument(
        "--metrics-root",
        default=None,
        help="Override for MetricsStore root (else default_root()).",
    )
    parser.add_argument(
        "--limit-markets",
        type=int,
        default=None,
        help="Smoke-test knob — process at most N markets (by volume desc).",
    )
    parser.add_argument(
        "--skip-signal",
        action="append",
        default=[],
        help="Signal id to omit (repeatable). Added to REPLAY_SKIP_SIGNALS.",
    )
    parser.add_argument(
        "--force-refresh-markets",
        action="store_true",
        help="Ignore the gamma market-enum cache and re-enumerate.",
    )
    parser.add_argument(
        "--force-refresh-trades",
        action="store_true",
        help="Ignore per-market trade caches and re-fetch from data-api.",
    )
    parser.add_argument(
        "--fetch-workers",
        type=int,
        default=DEFAULT_FETCH_WORKERS,
        help="Concurrent workers fetching per-market trade pages.",
    )
    parser.add_argument(
        "--min-volume",
        type=float,
        default=DEFAULT_MIN_VOLUME,
        help="Skip markets whose all-time volumeNum is below this threshold.",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)
    if end_date < start_date:
        LOG.error("--end must be >= --start")
        return 2

    cache_root = Path(args.trade_cache_root).expanduser()
    trade_cache_dir = cache_root / "trades"
    market_cache_path = cache_root / f"markets-{start_date}-{end_date}.json"

    metrics_root = Path(args.metrics_root).expanduser() if args.metrics_root else default_root()
    store = MetricsStore(metrics_root)

    # ── Stage 1: enumerate markets ────────────────────────────────
    markets = enumerate_markets(
        start=start_date,
        end=end_date,
        cache_path=market_cache_path,
        force_refresh=args.force_refresh_markets,
    )

    if args.markets_filter:
        if args.markets_filter.startswith("["):
            wanted = {c.lower() for c in json.loads(args.markets_filter)}
        else:
            wanted = {
                c.lower()
                for c in json.loads(Path(args.markets_filter).read_text())
            }
        markets = [m for m in markets if m["condition_id"].lower() in wanted]
        LOG.info("markets-filter: %d markets after filter", len(markets))

    # Drop zero-volume and sub-threshold markets upfront — they
    # demonstrably have no trades in any window.
    if args.min_volume > 0:
        pre = len(markets)
        markets = [m for m in markets if m.get("volumeNum", 0) >= args.min_volume]
        LOG.info(
            "min-volume: kept %d/%d markets with volumeNum >= %s",
            len(markets), pre, args.min_volume,
        )

    if args.limit_markets is not None:
        markets = markets[: args.limit_markets]
        LOG.info("limit-markets: processing top %d by volume", len(markets))

    # market_meta — gamma rows as `SignalContext.market_meta`.
    market_meta: dict[str, dict[str, Any]] = {}

    # ── Stage 2: fetch trades per market ──────────────────────────
    trade_window_start = datetime.combine(start_date, dtime.min, tzinfo=UTC)
    trade_window_end = datetime.combine(end_date, dtime.max, tzinfo=UTC) + timedelta(
        microseconds=1
    )

    # Populate market_meta skeletons upfront so every worker can
    # write `lastTradePrice` without racing on the dict shape.
    for m in markets:
        cid = m["condition_id"]
        market_meta.setdefault(
            cid,
            {
                "conditionId": cid,
                "question": m.get("question", ""),
                "slug": m.get("slug", ""),
                "startDate": m.get("startDate", ""),
                "endDate": m.get("endDate", ""),
                "closed": m.get("closed", False),
                "volumeNum": m.get("volumeNum", 0),
                # volume24hr is a live field — unknown in replay.
                # Signals that read it are skipped by default.
                "volume24hr": 0,
                "liquidityClob": 0,
                "category": "",
            },
        )

    t0 = time.time()
    trades_by_day: dict[date, list[dict[str, Any]]] = defaultdict(list)
    truncated_markets = 0
    total_trades = 0
    markets_with_trades = 0

    def _fetch_one(m: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
        cid = m["condition_id"]
        # Each worker owns its own httpx.Client — threading.Client
        # isn't safe to share across threads for httpx < 0.27.
        with httpx.Client(timeout=60.0) as client:
            try:
                rows, truncated = fetch_market_trades(
                    market_id=cid,
                    start=trade_window_start,
                    end=trade_window_end,
                    cache_dir=trade_cache_dir,
                    client=client,
                    force_refresh=args.force_refresh_trades,
                )
            except Exception:
                LOG.exception("market=%s fetch failed; skipping", cid)
                return m, [], False
        return m, rows, truncated

    done_count = 0
    with ThreadPoolExecutor(max_workers=args.fetch_workers) as pool:
        futures = [pool.submit(_fetch_one, m) for m in markets]
        for fut in as_completed(futures):
            m, rows, truncated = fut.result()
            cid = m["condition_id"]
            done_count += 1
            if truncated:
                truncated_markets += 1
                LOG.warning(
                    "market=%s truncated at offset wall (slug=%s)",
                    cid, m.get("slug", ""),
                )
            if rows:
                markets_with_trades += 1
                total_trades += len(rows)
                latest_ts: int = -1
                latest_price: float | None = None
                for row in rows:
                    ts = int(row.get("timestamp", 0) or 0)
                    if ts <= 0:
                        continue
                    d = datetime.fromtimestamp(ts, UTC).date()
                    if start_date <= d <= end_date:
                        trades_by_day[d].append(row)
                    if ts > latest_ts:
                        latest_ts = ts
                        try:
                            latest_price = float(row.get("price", 0) or 0)
                        except (TypeError, ValueError):
                            latest_price = None
                # Synthesise a `lastTradePrice` from the trade stream
                # so the price-band gate has something to read. See
                # BACKFILL-CAVEATS.md for the bias analysis.
                if latest_price is not None:
                    market_meta[cid]["lastTradePrice"] = latest_price
            if done_count % 250 == 0:
                elapsed = time.time() - t0
                LOG.info(
                    "fetch progress: %d/%d markets, %d trades so far "
                    "(%.1f min elapsed)",
                    done_count, len(markets), total_trades, elapsed / 60,
                )

    fetch_elapsed = time.time() - t0
    LOG.info(
        "trade fetch done: %d trades across %d/%d markets, %d truncated, "
        "%.1f min",
        total_trades, markets_with_trades, len(markets),
        truncated_markets, fetch_elapsed / 60,
    )

    # ── Stage 3: filter registry for replay ───────────────────────
    skip_ids = REPLAY_SKIP_SIGNALS | set(args.skip_signal)
    LOG.info("replay: skipping signals %s", sorted(skip_ids))
    original_registry = _filter_registry(skip_ids)

    # ── Stage 4: per-day replay ───────────────────────────────────
    days = _daterange(start_date, end_date)
    replayed = 0
    try:
        for d in days:
            t_day = time.time()
            try:
                n_trades, n_wallets = replay_day(
                    day=d,
                    trades_by_day=trades_by_day,
                    market_meta=market_meta,
                    store=store,
                    source_label=f"backfill (data-api, {len(markets)} markets)",
                )
            except Exception:
                LOG.exception("day=%s replay failed; skipping", d.isoformat())
                continue
            replayed += 1
            LOG.info(
                "day=%s: %d trades, %d wallets, %.1fs",
                d.isoformat(), n_trades, n_wallets, time.time() - t_day,
            )
    finally:
        _restore_registry(original_registry)

    total_elapsed = time.time() - t0
    LOG.info(
        "BACKFILL COMPLETE: %d days replayed, %d markets enumerated "
        "(%d with trades), %d total trades, %d truncated markets, "
        "%.1f min wall-clock.",
        replayed, len(markets), markets_with_trades, total_trades,
        truncated_markets, total_elapsed / 60,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
