#!/usr/bin/env python3
"""Drain bounce notifications into the `email_bounces` / `subscribers` tables.

Accepts DSN records as newline-delimited JSON from either stdin or a file
path. Each record must carry at least `email`, `bounce_type`, and
`message_id` fields; optional `diagnostic` and `reported_at` are recorded
verbatim.

Typical upstream: a sidecar that tails the Exim relay's bounce log and
emits one JSON record per DSN. Keeping that sidecar out-of-tree means
this script stays dialect-agnostic — we can point it at Postfix /
SendGrid / SES / etc. by plugging in a different producer.

Usage:
    cat bounces.jsonl | uv run python scripts/bounce-drain.py
    uv run python scripts/bounce-drain.py --input bounces.jsonl

Schedule via ami-cron (plan calls for */10 * * * *) alongside the tail
sidecar that writes the jsonl file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, TextIO

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from polymarket_insider_tracker.config import get_settings
from polymarket_insider_tracker.storage.repos import (
    EmailBounceDTO,
    EmailBounceRepository,
    EmailDeliveryRepository,
    SubscribersRepository,
)

LOG = logging.getLogger("bounce-drain")


@dataclass(frozen=True)
class BounceRecord:
    """Parsed bounce event from the upstream producer."""

    email: str
    bounce_type: str  # hard | soft | challenge | unknown
    message_id: str | None
    diagnostic: str | None
    reported_at: datetime


def parse_bounce_line(line: str) -> BounceRecord | None:
    """Parse one jsonl record. Returns None on malformed input (logged)."""
    line = line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        LOG.warning("skipping malformed line: %s", exc)
        return None

    email = payload.get("email")
    bounce_type = payload.get("bounce_type", "unknown")
    if not email or bounce_type not in ("hard", "soft", "challenge", "unknown"):
        LOG.warning("skipping record with missing/invalid fields: %s", payload)
        return None

    raw_ts = payload.get("reported_at")
    if isinstance(raw_ts, (int, float)):
        reported_at = datetime.fromtimestamp(float(raw_ts), tz=UTC)
    else:
        try:
            reported_at = datetime.fromisoformat(str(raw_ts))
        except (TypeError, ValueError):
            reported_at = datetime.now(UTC)

    return BounceRecord(
        email=str(email).strip().lower(),
        bounce_type=bounce_type,
        message_id=(str(payload["message_id"]) if payload.get("message_id") else None),
        diagnostic=payload.get("diagnostic"),
        reported_at=reported_at,
    )


def iter_bounces(source: TextIO) -> Iterable[BounceRecord]:
    """Yield BounceRecord instances, one per valid line."""
    for line in source:
        record = parse_bounce_line(line)
        if record is not None:
            yield record


async def drain(records: Iterable[BounceRecord]) -> int:
    """Persist the records; return the number successfully recorded."""
    settings = get_settings()
    engine = create_async_engine(settings.database.url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    count = 0
    try:
        async with factory() as session:
            bounces = EmailBounceRepository(session)
            deliveries = EmailDeliveryRepository(session)
            subscribers = SubscribersRepository(session)

            for record in records:
                # Correlate with the delivery ledger when we have a
                # message_id. The ledger row's id anchors the bounce
                # back to the send attempt (REQ-MAIL-131).
                delivery_id: int | None = None
                if record.message_id:
                    delivery = await deliveries.find_by_message_id(record.message_id)
                    if delivery is not None:
                        delivery_id = delivery.id

                await bounces.record(
                    EmailBounceDTO(
                        email=record.email,
                        bounce_type=record.bounce_type,
                        reported_at=record.reported_at,
                        delivery_id=delivery_id,
                        diagnostic=record.diagnostic,
                    )
                )
                # REQ-MAIL-115 threshold check — subscriber flips to
                # `bounced` after N consecutive hard bounces.
                await subscribers.record_bounce(
                    email=record.email,
                    bounce_type=record.bounce_type,
                )
                count += 1

            await session.commit()
    finally:
        await engine.dispose()
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Drain bounce DSNs into the DB")
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Path to a jsonl file. Defaults to stdin.",
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

    if args.input is not None:
        with args.input.open("r", encoding="utf-8") as fh:
            records = list(iter_bounces(fh))
    else:
        records = list(iter_bounces(sys.stdin))

    if not records:
        LOG.info("no bounce records to drain")
        return 0

    count = asyncio.run(drain(records))
    LOG.info("drained %d bounce record(s)", count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
