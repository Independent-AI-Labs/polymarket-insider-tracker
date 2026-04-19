#!/usr/bin/env python3
"""Compute shared-origin funding clusters from `funding_transfers`.

Phase 5 of docs/IMPLEMENTATION-TODOS.md. Runs every 15 minutes via
ami-cron (documented in README) alongside the other newsletter
sidecars.

Workflow
    1. Collect the set of wallet addresses that received USDC in the
       last `--window-hours` window (default 48).
    2. Call `collect_shared_origins` to group them by origin.
    3. `persist_clusters` writes one `shared_origin` relationship
       per unordered wallet pair.

Idempotent by construction — `WalletRelationshipRepository.upsert`
dedups on the unique constraint.

Usage:
    uv run python scripts/compute-funding-clusters.py
    uv run python scripts/compute-funding-clusters.py --window-hours 72
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from polymarket_insider_tracker.config import get_settings
from polymarket_insider_tracker.profiler.funding_graph import (
    DEFAULT_MIN_CLUSTER_SIZE,
    DEFAULT_WINDOW_HOURS,
    collect_shared_origins,
    persist_clusters,
)
from polymarket_insider_tracker.storage.models import FundingTransferModel

LOG = logging.getLogger("funding-clusters")


async def run(window_hours: int, min_cluster_size: int) -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database.url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)

    try:
        async with factory() as session:
            # Pull every wallet that received USDC in the window.
            stmt = (
                select(FundingTransferModel.to_address)
                .where(FundingTransferModel.timestamp >= cutoff)
                .distinct()
            )
            result = await session.execute(stmt)
            wallets = [row[0] for row in result.all()]
            if not wallets:
                LOG.info("no funded wallets in the last %dh", window_hours)
                return 0

            LOG.info(
                "collecting shared-origin clusters for %d wallet(s)",
                len(wallets),
            )
            clusters = await collect_shared_origins(
                session,
                wallets,
                window_hours=window_hours,
                min_cluster_size=min_cluster_size,
            )
            edges = await persist_clusters(session, clusters)
            await session.commit()
            LOG.info(
                "persisted %d shared_origin edge(s) across %d cluster(s)",
                edges,
                len(clusters),
            )
            return 0
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute funding clusters")
    parser.add_argument(
        "--window-hours",
        type=int,
        default=DEFAULT_WINDOW_HOURS,
        help=f"Look-back window in hours (default: {DEFAULT_WINDOW_HOURS})",
    )
    parser.add_argument(
        "--min-cluster-size",
        type=int,
        default=DEFAULT_MIN_CLUSTER_SIZE,
        help=f"Minimum wallets per cluster (default: {DEFAULT_MIN_CLUSTER_SIZE})",
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
    return asyncio.run(run(args.window_hours, args.min_cluster_size))


if __name__ == "__main__":
    sys.exit(main())
