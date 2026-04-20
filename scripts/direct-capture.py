#!/usr/bin/env python3
"""Minimal direct-to-jsonl capture — bypasses Redis for short-window tests.

Unlike `capture-trades.py` which reads from the Redis `trades` stream
(requires the main pipeline to be running), this tool subscribes to
the Polymarket WebSocket directly and writes every trade to a jsonl
file. Used by Phase 9 validation — a 30-minute live capture we can
replay through the detector stack without spinning up the full
pipeline stack.

Protocol auto-detect: when the configured WS URL points at
`/ws/market` (the CLOB market channel), this script first fetches
active markets from gamma-api.polymarket.com to build the
`assets_ids` subscription payload Polymarket requires. Otherwise the
legacy `topic: activity` feed is used, which needs no asset list.

Usage:
    uv run python scripts/direct-capture.py \\
        --output data/captures/live-capture.jsonl --duration 300
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from polymarket_insider_tracker.backtest.replay import trade_event_to_record
from polymarket_insider_tracker.config import get_settings
from polymarket_insider_tracker.ingestor.data_api import DataAPITradePoller
from polymarket_insider_tracker.ingestor.models import TradeEvent
from polymarket_insider_tracker.ingestor.websocket import (
    SubscriptionMode,
    TradeStreamHandler,
)

LOG = logging.getLogger("direct-capture")

# gamma-api is pinned in /etc/hosts to a Cloudflare IP, so this
# hostname resolves without touching upstream DNS.
GAMMA_API_BASE = "https://gamma-api.polymarket.com"


async def _fetch_active_asset_ids(
    limit: int, top_by: str = "volume24hr"
) -> tuple[list[str], dict[str, dict[str, str]]]:
    """Fetch the top-N active markets and return (asset_ids, meta).

    `meta` maps asset_id -> {condition_id, market_slug, event_slug,
    event_title, outcome, outcome_index} so the WS handler can
    populate TradeEvent fields that `last_trade_price` frames don't
    carry.
    """
    asset_ids: list[str] = []
    meta: dict[str, dict[str, str]] = {}

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{GAMMA_API_BASE}/markets",
            params={
                "limit": limit,
                "order": top_by,
                "ascending": "false",
                "active": "true",
                "closed": "false",
            },
        )
        resp.raise_for_status()
        markets: list[dict[str, Any]] = resp.json()

    for market in markets:
        condition_id = str(market.get("conditionId") or "")
        slug = str(market.get("slug") or "")
        event = market.get("events") or []
        event_slug = str(event[0].get("slug") if event else "") or ""
        event_title = str(event[0].get("title") if event else "") or ""

        # `clobTokenIds` ships as a JSON-encoded string on gamma — e.g.
        # '["123...", "456..."]'. `outcomes` is the same shape.
        token_ids_raw = market.get("clobTokenIds") or "[]"
        outcomes_raw = market.get("outcomes") or "[]"
        try:
            token_ids = (
                json.loads(token_ids_raw)
                if isinstance(token_ids_raw, str)
                else list(token_ids_raw)
            )
            outcomes = (
                json.loads(outcomes_raw)
                if isinstance(outcomes_raw, str)
                else list(outcomes_raw)
            )
        except json.JSONDecodeError:
            continue

        for idx, tok in enumerate(token_ids):
            tok_s = str(tok)
            if not tok_s:
                continue
            asset_ids.append(tok_s)
            meta[tok_s] = {
                "condition_id": condition_id,
                "market_slug": slug,
                "event_slug": event_slug,
                "event_title": event_title,
                "outcome": str(outcomes[idx]) if idx < len(outcomes) else "",
                "outcome_index": str(idx),
            }

    LOG.info(
        "gamma: fetched %d markets → %d asset_ids for CLOB subscription",
        len(markets),
        len(asset_ids),
    )
    return asset_ids, meta


async def run(
    output_path: Path,
    duration_seconds: int,
    *,
    market_limit: int,
    source: str,
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    started = datetime.now(UTC)
    stop_event = asyncio.Event()

    def _request_stop(*_: object) -> None:
        LOG.info("stop requested")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_stop)

    with output_path.open("a", encoding="utf-8") as fh:

        async def on_trade(event: TradeEvent) -> None:
            nonlocal count
            fh.write(json.dumps(trade_event_to_record(event)) + "\n")
            fh.flush()
            count += 1
            if count % 50 == 0:
                elapsed = (datetime.now(UTC) - started).total_seconds()
                LOG.info("captured %d events (%.0fs elapsed)", count, elapsed)

        settings = get_settings()

        if source == "data-api":
            LOG.info("source=data-api — polling data-api.polymarket.com/trades")
            poller = DataAPITradePoller(on_trade=on_trade)
            task = asyncio.create_task(poller.start())
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=duration_seconds)
            except asyncio.TimeoutError:
                LOG.info("duration reached (%ds)", duration_seconds)
            finally:
                await poller.stop()
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001
                    if not isinstance(exc, asyncio.CancelledError):
                        LOG.warning("poller exited with %s", exc)
                LOG.info(
                    "data-api stats: polls=%d rows=%d emitted=%d dup=%d errors=%d",
                    poller.stats.polls,
                    poller.stats.rows_fetched,
                    poller.stats.trades_emitted,
                    poller.stats.duplicates_skipped,
                    poller.stats.http_errors,
                )
        else:
            host = settings.polymarket.ws_url
            asset_ids: list[str] = []
            asset_meta: dict[str, dict[str, str]] = {}
            mode = (
                SubscriptionMode.CLOB_MARKET
                if "/ws/market" in host
                else SubscriptionMode.ACTIVITY
            )
            if mode is SubscriptionMode.CLOB_MARKET:
                LOG.info("CLOB mode detected (%s) — fetching asset_ids from gamma", host)
                asset_ids, asset_meta = await _fetch_active_asset_ids(market_limit)
                if not asset_ids:
                    LOG.error("no asset_ids — CLOB subscription would be empty; aborting")
                    return 2

            handler = TradeStreamHandler(
                on_trade=on_trade,
                host=host,
                mode=mode,
                asset_ids=asset_ids,
                asset_id_to_condition=asset_meta,
            )
            task = asyncio.create_task(handler.start())
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=duration_seconds)
            except asyncio.TimeoutError:
                LOG.info("duration reached (%ds)", duration_seconds)
            finally:
                await handler.stop()
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001
                    if not isinstance(exc, asyncio.CancelledError):
                        LOG.warning("handler exited with %s", exc)

    LOG.info("captured %d events to %s", count, output_path)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Direct WS-to-jsonl capture")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/captures") / f"live-{datetime.now(UTC):%Y%m%dT%H%M%S}.jsonl",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=600,
        help="How many seconds to capture (default 600 = 10 minutes)",
    )
    parser.add_argument(
        "--market-limit",
        type=int,
        default=200,
        help=(
            "Max markets to subscribe to in CLOB mode — each contributes 2 "
            "asset_ids. Polymarket does not document a hard cap but the CLOB "
            "market channel has been observed to accept ~500 asset_ids per "
            "subscribe. Default 200 markets (~400 asset_ids)."
        ),
    )
    parser.add_argument(
        "--source",
        choices=["clob-ws", "data-api"],
        default="data-api",
        help=(
            "Trade source. `data-api` polls the public /trades REST "
            "endpoint — slower (~2-5s lag) but carries proxyWallet. "
            "`clob-ws` uses the CLOB /ws/market channel (sub-second, "
            "but anonymized). Default data-api since wallets are the "
            "whole point of insider tracking."
        ),
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return asyncio.run(
        run(
            args.output,
            args.duration,
            market_limit=args.market_limit,
            source=args.source,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
