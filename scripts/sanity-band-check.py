#!/usr/bin/env python3
"""Enforce the CI sanity band on detector_metrics rows.

Task 9.2.4 of IMPLEMENTATION-TODOS. Queries the last N days of
`detector_metrics` and exits non-zero if any signal's precision
falls outside the documented band `[min, max]`.

The band catches two failure modes the scenario tests can't:
- Upper bound (≥ 0.95): synthetic fixtures leaking into the
  production DB would hit 100% precision on whatever signal they
  exercised.
- Lower bound (≤ 0.20): detector regression (threshold drift,
  data-quality collapse, or the Polymarket surface shifting).

Usage:
    uv run python scripts/sanity-band-check.py
    uv run python scripts/sanity-band-check.py --days 7 --signal combined
    uv run python scripts/sanity-band-check.py --min 0.15 --max 0.98
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from polymarket_insider_tracker.config import get_settings
from polymarket_insider_tracker.storage.repos import DetectorMetricsRepository

LOG = logging.getLogger("sanity-band")


async def run(
    *,
    days: int,
    signal: str | None,
    min_precision: float,
    max_precision: float,
    require_rows: bool,
) -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database.url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    window_end = datetime.now(UTC)
    window_start = window_end - timedelta(days=days)
    exit_code = 0
    try:
        async with factory() as session:
            repo = DetectorMetricsRepository(session)
            rows = await repo.list_for_window(window_start, window_end)
            if signal:
                rows = [r for r in rows if r.signal == signal]
            if not rows:
                msg = (
                    f"no detector_metrics rows in the last {days}d "
                    f"(signal={signal or 'any'})"
                )
                if require_rows:
                    LOG.error("%s — failing gate", msg)
                    return 2
                LOG.warning("%s — gate skipped", msg)
                return 0

            for row in rows:
                # Convert to float for comparison; None precision (no
                # hits+misses) isn't scoreable — log it but don't fail.
                if row.precision is None:
                    LOG.info(
                        "%s  %s: no hits/misses yet (alerts=%d pending=%d)",
                        row.window_start.date(),
                        row.signal,
                        row.alerts_total,
                        row.pending,
                    )
                    continue
                precision = float(row.precision)
                in_band = min_precision <= precision <= max_precision
                marker = "OK " if in_band else "FAIL"
                LOG.info(
                    "%s  %s  %s  precision=%.4f  alerts=%d  pnl=%s bps",
                    row.window_start.date(),
                    row.signal,
                    marker,
                    precision,
                    row.alerts_total,
                    row.pnl_uplift_bps,
                )
                if not in_band:
                    exit_code = 1
    finally:
        await engine.dispose()
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detector-metrics sanity-band check")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument(
        "--signal",
        default=None,
        help="Filter to one signal name (default: all signals)",
    )
    parser.add_argument("--min", dest="min_p", type=float, default=0.20)
    parser.add_argument("--max", dest="max_p", type=float, default=0.95)
    parser.add_argument(
        "--require-rows",
        action="store_true",
        help="Fail if no metrics rows exist (CI-strict mode).",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return asyncio.run(
        run(
            days=args.days,
            signal=args.signal,
            min_precision=args.min_p,
            max_precision=args.max_p,
            require_rows=args.require_rows,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
