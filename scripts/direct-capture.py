#!/usr/bin/env python3
"""Minimal direct-to-jsonl capture — bypasses Redis for short-window tests.

Unlike `capture-trades.py` which reads from the Redis `trades` stream
(requires the main pipeline to be running), this tool subscribes to
the Polymarket WebSocket directly and writes every trade to a jsonl
file. Used by Phase 9 validation — a 30-minute live capture we can
replay through the detector stack without spinning up the full
pipeline stack.

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
from datetime import UTC, datetime, timedelta
from pathlib import Path

from polymarket_insider_tracker.backtest.replay import trade_event_to_record
from polymarket_insider_tracker.config import get_settings
from polymarket_insider_tracker.ingestor.models import TradeEvent
from polymarket_insider_tracker.ingestor.websocket import TradeStreamHandler

LOG = logging.getLogger("direct-capture")


async def run(output_path: Path, duration_seconds: int) -> int:
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

        # WebSocket host — prefer the env-configured URL so the
        # capture uses the same endpoint as the main pipeline rather
        # than the module-level default (which points at a retired
        # hostname).
        settings = get_settings()
        host = settings.polymarket.ws_url
        handler = TradeStreamHandler(on_trade=on_trade, host=host)
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
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return asyncio.run(run(args.output, args.duration))


if __name__ == "__main__":
    sys.exit(main())
