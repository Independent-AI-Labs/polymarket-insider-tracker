#!/usr/bin/env python3
"""Capture the live WebSocket trade stream into a daily jsonl file.

Runs alongside the main pipeline as a parallel consumer on the Redis
`trades` stream (the pipeline already publishes every TradeEvent
there via `EventPublisher`). Reads via a dedicated consumer group so
the main pipeline's consumer isn't starved.

Output files land in `data/captures/capture-YYYYMMDD.jsonl`, rotated
at UTC midnight. `data/captures/` is gitignored.

Usage:
    uv run python scripts/capture-trades.py
    uv run python scripts/capture-trades.py --output-dir /tmp/captures

Stop with SIGINT/SIGTERM — the current file gets fsynced on exit.
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

from redis.asyncio import Redis

from polymarket_insider_tracker.backtest.replay import trade_event_to_record
from polymarket_insider_tracker.config import get_settings
from polymarket_insider_tracker.ingestor.publisher import (
    DEFAULT_STREAM_NAME,
    EventPublisher,
)

LOG = logging.getLogger("capture-trades")

CONSUMER_GROUP = "trade-capture"
CONSUMER_NAME_PREFIX = "capture"
READ_COUNT = 100            # max entries per XREADGROUP
READ_BLOCK_MS = 5000        # block up to 5 s waiting for new entries


class DailyRotator:
    """File handle that rolls at UTC midnight.

    Simpler than logging.TimedRotatingFileHandler because we only need
    a single writer and want explicit fsync on exit.
    """

    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._current_date: datetime | None = None
        self._handle = None  # type: ignore[assignment]

    def _path_for(self, day: datetime) -> Path:
        return self._output_dir / f"capture-{day:%Y%m%d}.jsonl"

    def write(self, record: dict[str, str]) -> None:
        now = datetime.now(UTC)
        current_day = now.date()
        if self._current_date is None or current_day != self._current_date:
            self.close()
            path = self._path_for(now)
            self._handle = path.open("a", encoding="utf-8")
            self._current_date = current_day
            LOG.info("rotated capture file -> %s", path)
        assert self._handle is not None
        self._handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._handle.flush()

    def close(self) -> None:
        if self._handle is not None:
            self._handle.flush()
            # Best-effort fsync — if the file descriptor is bad we'd
            # rather lose the fsync than crash on shutdown.
            try:
                import os
                os.fsync(self._handle.fileno())
            except OSError as exc:
                LOG.warning("fsync failed: %s", exc)
            self._handle.close()
            self._handle = None


async def run(output_dir: Path) -> int:
    """Main capture loop. Returns the process exit code."""
    settings = get_settings()
    redis = Redis.from_url(settings.redis.url, decode_responses=False)
    publisher = EventPublisher(redis=redis, stream_name=DEFAULT_STREAM_NAME)

    try:
        await publisher.create_consumer_group(CONSUMER_GROUP, start_id="$", mkstream=True)
        LOG.info("consumer group ready: %s", CONSUMER_GROUP)
    except Exception as exc:  # noqa: BLE001 — group-exists is fine
        if "BUSYGROUP" not in str(exc):
            LOG.exception("failed to create consumer group")
            return 2
        LOG.info("consumer group already exists: %s", CONSUMER_GROUP)

    rotator = DailyRotator(output_dir)
    consumer_name = f"{CONSUMER_NAME_PREFIX}-{datetime.now(UTC):%Y%m%dT%H%M%S}"

    stop_event = asyncio.Event()

    def _request_stop(*_: object) -> None:
        LOG.info("stop requested")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_stop)

    count = 0
    try:
        while not stop_event.is_set():
            entries = await publisher.read_events(
                group_name=CONSUMER_GROUP,
                consumer_name=consumer_name,
                count=READ_COUNT,
                block_ms=READ_BLOCK_MS,
            )
            for entry in entries:
                rotator.write(trade_event_to_record(entry.event))
                count += 1
            if entries and count % 1000 == 0:
                LOG.info("captured %d events", count)
    finally:
        rotator.close()
        await redis.aclose()
        LOG.info("captured %d events total", count)

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Polymarket trade-stream capture")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/captures"),
        help="Directory for daily capture files (default: data/captures)",
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

    return asyncio.run(run(args.output_dir))


if __name__ == "__main__":
    sys.exit(main())
