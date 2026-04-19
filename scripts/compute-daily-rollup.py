#!/usr/bin/env python3
"""Compute yesterday's alert-daily-rollup rows.

The daily newsletter reads `alert_daily_rollup`; this script is what
fills it. Pulls alert records from Redis (populated by
`alerter.history.AlertHistory.record`) for the previous UTC day,
groups by (market, signal), and upserts one row per group.

Schedule via ami-cron at `5 0 * * *` UTC so the rollup is ready
before the daily newsletter runs at 13:00 UTC.

Usage:
    uv run python scripts/compute-daily-rollup.py               # yesterday
    uv run python scripts/compute-daily-rollup.py --day 2026-04-18
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path  # noqa: F401 — re-exported via ami-cron env

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from polymarket_insider_tracker.config import get_settings
from polymarket_insider_tracker.storage.repos import (
    AlertRollupDTO,
    AlertRollupRepository,
)

LOG = logging.getLogger("daily-rollup")

# Redis keys set by AlertHistory.record().
ALERT_INDEX_TIME = "alert:index:time"        # sorted set — alert_id by unix ts
ALERT_RECORD_PREFIX = "alert:record:"        # JSON payload per alert_id


async def _collect_alerts(
    redis: Redis, *, day_start: datetime, day_end: datetime
) -> list[dict]:
    """Return every alert record written during [day_start, day_end)."""
    start_score = day_start.timestamp()
    end_score = day_end.timestamp()
    alert_ids = await redis.zrangebyscore(
        ALERT_INDEX_TIME, min=start_score, max=f"({end_score}"
    )
    if not alert_ids:
        return []

    # Fetch each record individually — MGET returns bytes, which we
    # JSON-decode. Records that have expired (past the 30-day
    # retention) are skipped.
    keys = [
        f"{ALERT_RECORD_PREFIX}{aid.decode() if isinstance(aid, bytes) else aid}"
        for aid in alert_ids
    ]
    raw = await redis.mget(*keys)
    out: list[dict] = []
    for payload in raw:
        if payload is None:
            continue
        try:
            out.append(json.loads(payload))
        except json.JSONDecodeError as exc:
            LOG.warning("skipping malformed alert record: %s", exc)
    return out


def _aggregate(records: list[dict], *, day: date) -> list[AlertRollupDTO]:
    """Group by (market_id, signal) and emit one row per group."""
    # signal -> market_id -> {"count", "wallets" (set), "notional" (Decimal)}
    bucket: dict[str, dict[str, dict]] = {}
    for rec in records:
        market_id = str(rec.get("market_id", ""))
        wallet = str(rec.get("wallet_address", ""))
        notional_raw = rec.get("trade_size")
        try:
            notional = Decimal(notional_raw) if notional_raw is not None else None
        except (TypeError, ValueError, ArithmeticError):
            notional = None

        # signals_triggered is a list of signal name strings on the record.
        signals = rec.get("signals_triggered") or []
        if not isinstance(signals, list) or not signals:
            signals = ["unclassified"]

        for signal in signals:
            signal_key = str(signal)
            by_market = bucket.setdefault(signal_key, {})
            stats = by_market.setdefault(
                market_id,
                {"count": 0, "wallets": set(), "notional": Decimal(0), "seen_notional": False},
            )
            stats["count"] += 1
            stats["wallets"].add(wallet.lower())
            if notional is not None:
                stats["notional"] += notional
                stats["seen_notional"] = True

    rows: list[AlertRollupDTO] = []
    for signal_key, by_market in bucket.items():
        for market_id, stats in by_market.items():
            rows.append(
                AlertRollupDTO(
                    day=day,
                    market_id=market_id,
                    signal=signal_key,
                    alert_count=stats["count"],
                    unique_wallets=len(stats["wallets"]),
                    total_notional=(
                        stats["notional"] if stats["seen_notional"] else None
                    ),
                )
            )
    return rows


async def run(target_day: date) -> int:
    settings = get_settings()

    day_start = datetime.combine(target_day, time.min, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)

    redis = Redis.from_url(settings.redis.url)
    engine = create_async_engine(settings.database.url)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    try:
        records = await _collect_alerts(redis, day_start=day_start, day_end=day_end)
        rows = _aggregate(records, day=target_day)
        LOG.info(
            "collected %d alert records -> %d rollup rows for %s",
            len(records),
            len(rows),
            target_day,
        )
        async with session_factory() as session:
            repo = AlertRollupRepository(session)
            for row in rows:
                await repo.upsert(row)
            await session.commit()
    finally:
        await redis.aclose()
        await engine.dispose()

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute alert-daily-rollup rows")
    parser.add_argument(
        "--day",
        type=lambda s: date.fromisoformat(s),
        default=(datetime.now(UTC) - timedelta(days=1)).date(),
        help="Target UTC day (default: yesterday)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return asyncio.run(run(args.day))


if __name__ == "__main__":
    sys.exit(main())
