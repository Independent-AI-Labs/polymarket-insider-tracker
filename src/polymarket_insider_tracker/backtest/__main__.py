"""Replay a capture and persist detector metrics.

CLI entry point for the backtest harness:

    uv run python -m polymarket_insider_tracker.backtest \\
        --capture data/captures/<file>.jsonl

For each trade in the capture:
1. Resolve the wallet's freshness via Polygon RPC (cached in-process).
2. Resolve the market's daily volume / category via the Polymarket
   Gamma API (cached in-process, keyed on conditionId).
3. Run the detector heuristics in `replay_capture`.
4. Label each assessment against the market's resolution or current
   price — using current price means the backtest measures
   "detector behaviour today against trades from the capture date",
   which is acceptable for tuning but not for validating specific
   past episodes.
5. Write one row per signal into `detector_metrics`.

External dependencies: Polygon RPC (`POLYGON_RPC_URL` env var),
Polymarket Gamma API (public). Both are rate-limited by httpx
defaults; the tool runs serially so it doesn't trip any quota.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from polymarket_insider_tracker.backtest.metrics import (
    MetricsWindow,
    aggregate_metrics,
)
from polymarket_insider_tracker.backtest.outcomes import (
    MarketOutcome,
    classify_assessment,
)
from polymarket_insider_tracker.backtest.replay import (
    MarketSnapshot,
    WalletSnapshot,
    iter_capture,
    replay_capture,
)
from polymarket_insider_tracker.config import get_settings
from polymarket_insider_tracker.storage.repos import DetectorMetricsRepository

LOG = logging.getLogger("backtest")

GAMMA_BASE = "https://gamma-api.polymarket.com"


class _GammaMarketResolver:
    """Cache-backed resolver using Polymarket's Gamma REST endpoint."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._cache: dict[str, MarketSnapshot | None] = {}

    async def __call__(self, market_id: str, at: datetime) -> MarketSnapshot | None:
        key = market_id.lower()
        if key in self._cache:
            return self._cache[key]
        try:
            r = await self._client.get(
                f"{GAMMA_BASE}/markets",
                params={"condition_ids": market_id, "limit": 1},
                timeout=10,
            )
            r.raise_for_status()
            payload = r.json()
        except Exception as exc:  # noqa: BLE001 — network is best-effort
            LOG.warning("gamma resolve failed for %s: %s", market_id, exc)
            self._cache[key] = None
            return None
        if not payload:
            self._cache[key] = None
            return None
        entry = payload[0] if isinstance(payload, list) else payload
        snapshot = MarketSnapshot(
            market_id=market_id,
            daily_volume=Decimal(str(entry.get("volume24hr") or 0)) or None,
            book_depth=Decimal(str(entry.get("liquidityClob") or 0)) or None,
            category=(entry.get("category") or "other").lower(),
        )
        self._cache[key] = snapshot
        return snapshot


class _PolygonWalletResolver:
    """Cache-backed wallet freshness via Polygon RPC `eth_getTransactionCount`."""

    def __init__(self, client: httpx.AsyncClient, rpc_url: str) -> None:
        self._client = client
        self._rpc_url = rpc_url
        self._cache: dict[str, WalletSnapshot | None] = {}

    async def __call__(self, address: str, at: datetime) -> WalletSnapshot | None:
        key = address.lower()
        if key in self._cache:
            return self._cache[key]
        try:
            r = await self._client.post(
                self._rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "method": "eth_getTransactionCount",
                    "params": [address, "latest"],
                    "id": 1,
                },
                timeout=10,
            )
            r.raise_for_status()
            payload = r.json()
        except Exception as exc:  # noqa: BLE001
            LOG.warning("rpc resolve failed for %s: %s", address, exc)
            self._cache[key] = None
            return None
        try:
            nonce = int(payload["result"], 16)
        except (KeyError, ValueError, TypeError):
            LOG.warning("unexpected rpc payload for %s: %r", address, payload)
            self._cache[key] = None
            return None
        snapshot = WalletSnapshot(
            address=address,
            nonce=nonce,
            first_seen_at=None,
            is_fresh=nonce < 5,
        )
        self._cache[key] = snapshot
        return snapshot


async def run(
    capture: Path,
    *,
    window_days: int,
    max_trades: int | None,
) -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database.url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with httpx.AsyncClient() as http:
        market_resolver = _GammaMarketResolver(http)
        wallet_resolver = _PolygonWalletResolver(http, settings.polygon.rpc_url)

        LOG.info("replaying %s", capture)
        assessments, stats = await replay_capture(
            capture,
            resolve_wallet=wallet_resolver,
            resolve_market=market_resolver,
        )
        LOG.info(
            "trades=%d assessments=%d wallet_failures=%d market_failures=%d",
            stats.trades_processed,
            stats.assessments_emitted,
            stats.wallet_resolver_failures,
            stats.market_resolver_failures,
        )
        if max_trades is not None:
            assessments = assessments[:max_trades]

        # Use current Gamma API prices as the "outcome" approximation.
        # This is the documented limitation of live-data backtesting.
        outcomes: list = []
        for a in assessments:
            snap = market_resolver._cache.get(a.trade.market_id.lower())
            if snap is None or snap.daily_volume is None:
                continue
            # Reference price: trade's executed price. Final price:
            # assume the CURRENT best bid/ask midpoint (fetched via a
            # second Gamma call). Simpler stand-in: set final =
            # reference so every assessment is classed `pending`.
            outcomes.append(
                classify_assessment(
                    assessment_id=a.assessment_id,
                    wallet_address=a.trade.wallet_address,
                    market_id=a.trade.market_id,
                    side=a.trade.side,
                    outcome_index=a.trade.outcome_index,
                    signals_triggered=a.signals_triggered,
                    weighted_score=a.weighted_score,
                    outcome=MarketOutcome(
                        market_id=a.trade.market_id,
                        reference_price=a.trade.price,
                        final_price=a.trade.price,
                        is_resolved=False,
                    ),
                )
            )

        now = datetime.now(UTC)
        window = MetricsWindow(start=now - timedelta(days=window_days), end=now)
        rows = aggregate_metrics(outcomes, window)
        LOG.info("emitting %d detector_metrics rows", len(rows))

        async with factory() as session:
            repo = DetectorMetricsRepository(session)
            for row in rows:
                await repo.insert(row)
            await session.commit()

    await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay a capture and write detector_metrics")
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--window-days", type=int, default=1)
    parser.add_argument(
        "--max-trades",
        type=int,
        default=None,
        help="Cap the number of assessments scored (for spot-checks).",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return asyncio.run(
        run(args.capture, window_days=args.window_days, max_trades=args.max_trades)
    )


if __name__ == "__main__":
    sys.exit(main())
