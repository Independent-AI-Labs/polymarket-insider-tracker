"""Shared helpers for the polymarket newsletter trilogy.

Each cadence (daily / weekly / monthly) has its own data builder and
Tera template but shares the himalaya delivery plumbing, target-list
filtering, and the tempfile-YAML handoff with `batch send`.

This module is imported by `send-report.py` (daily), plus the
new `weekly-newsletter.py` and `monthly-newsletter.py`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterable

import yaml

LOG = logging.getLogger("newsletter")

# Bold markdown `**X**` → HTML span. Pre-rendered in Python so Tera
# templates emit observations with `| safe` and stay straight iterator
# logic.
BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")


def render_bold(raw: str) -> str:
    """Convert `**X**` spans to `<strong>X</strong>`."""
    return BOLD_RE.sub(r"<strong>\1</strong>", raw)


def filter_targets(targets: list[dict], names: str | None) -> list[dict]:
    """Narrow the recipient list by `--targets a,b,c` or the `enabled` flag.

    Explicit `--targets` selection overrides `enabled=False`, so an
    operator can force-send to a normally-disabled archive address.
    """
    if names:
        requested = {t.strip() for t in names.split(",")}
        return [t for t in targets if t["name"] in requested]
    return [t for t in targets if t.get("enabled", True)]


def deliver_via_himalaya(
    rows: list[dict],
    *,
    template_path: Path,
    subject_template: str,
    account: str,
    rate: str = "5/min",
    attachments: Iterable[Path] = (),
    dry_run: bool = False,
    edition_id: str | None = None,
    cadence: str | None = None,
    ledger_writer: Callable[[str, str, list[dict], datetime, bool], None] | None = None,
) -> int:
    """Drive `himalaya batch send` against `rows`.

    `rows` is a list of per-recipient dicts; each dict must carry at
    least `email`, `name`, `subject` (the final rendered subject
    string — Tera renders `{{ subject }}` in the template).

    When `ledger_writer` is passed, this function also asks himalaya
    for JSON output and forwards the parsed result list to the writer
    so a caller can persist the email_deliveries ledger rows
    (REQ-MAIL-130). The writer signature is
    `writer(edition_id, cadence, result_entries, queued_at, dry_run)`.
    Keeping it callable-shaped means the tests can inject a fake
    without pulling in SQLAlchemy, and production wires
    `write_delivery_ledger` below.
    """
    if not rows:
        print("  [WARN] No delivery targets matched")
        return 0

    cmd: list[str] = []
    queued_at = datetime.now(UTC)

    # NamedTemporaryFile is the context manager; cleanup is guaranteed
    # on both success and exception.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", prefix="polymarket-targets-",
    ) as fh:
        yaml.safe_dump(rows, fh, allow_unicode=True, sort_keys=False)
        fh.flush()

        cmd = ["himalaya"]
        if ledger_writer is not None:
            # --output json is a global flag; it has to precede the
            # subcommand. We only switch on it when a ledger writer is
            # supplied to avoid noising up operator stdout for the
            # plain dry-run / one-off case.
            cmd.extend(["--output", "json"])
        cmd.extend([
            "batch", "send",
            "--account", account,
            "--template", str(template_path),
            "--data", fh.name,
            "--subject", subject_template,
            "--rate", rate,
            "--yes",
        ])
        for attachment in attachments:
            cmd.extend(["--attachment", str(attachment)])
        if dry_run:
            cmd.append("--dry-run")

        print(
            f"  → himalaya batch send → {len(rows)} recipient(s) via account "
            f"{account!r}"
        )
        for r in rows:
            print(f"    • {r['email']}  subject={r.get('subject', '(none)')!r}")

        result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"  [ERROR] himalaya batch send failed (rc={result.returncode})")
        if result.stderr:
            print(result.stderr.rstrip())
        if result.stdout:
            print(result.stdout.rstrip())
    else:
        if ledger_writer is not None:
            parsed = _parse_himalaya_summary(result.stdout)
            if parsed is not None and edition_id and cadence:
                try:
                    ledger_writer(edition_id, cadence, parsed, queued_at, dry_run)
                except Exception:  # noqa: BLE001 — ledger is best-effort
                    LOG.exception("ledger writer failed; send itself succeeded")
            # With JSON output we ate the operator-facing summary, so
            # emit a minimal one derived from the parsed result.
            if parsed is not None:
                sent = sum(1 for r in parsed if r.get("status") == "sent")
                dryr = sum(1 for r in parsed if r.get("status") == "dry-run")
                failed = sum(1 for r in parsed if r.get("status") == "failed")
                print(
                    f"    Batch complete: sent={sent} dry-run={dryr} failed={failed}"
                )
        else:
            out = result.stdout.strip()
            if out:
                for line in out.splitlines():
                    print(f"    {line}")

    return result.returncode


def _parse_himalaya_summary(raw: str) -> list[dict] | None:
    """Parse himalaya's `--output json` batch summary.

    Expected shape (see himalaya/src/batch/command/send.rs::BatchSummary):

        {"total": N, "sent": N, "failed": N, "results": [
            {"email": "...", "status": "sent", "message_id": "<id>"|null, ...}
        ]}

    Returns the `results` list on success, None when the output
    doesn't match the expected shape — callers degrade to "no ledger
    rows written this run" rather than crashing the send.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        LOG.warning("himalaya emitted non-JSON output; ledger skipped")
        return None
    results = payload.get("results")
    if not isinstance(results, list):
        LOG.warning("himalaya JSON missing `results` list; ledger skipped")
        return None
    return results


def write_delivery_ledger(
    session_factory,
    edition_id: str,
    cadence: str,
    result_entries: list[dict],
    queued_at: datetime,
    dry_run: bool,
) -> None:
    """Persist one email_deliveries row per himalaya result entry.

    Production `ledger_writer` for `deliver_via_himalaya`. Accepts
    SQLAlchemy's async_sessionmaker and runs an `asyncio.run` under
    the hood so the synchronous delivery path can call it. If the
    caller is already inside an event loop, pass a thin wrapper that
    schedules the coroutine on the running loop instead.
    """
    from polymarket_insider_tracker.storage.repos import (
        EmailDeliveryDTO,
        EmailDeliveryRepository,
    )

    async def _persist() -> None:
        async with session_factory() as session:
            repo = EmailDeliveryRepository(session)
            for entry in result_entries:
                outcome = "dry-run" if dry_run else entry.get("status", "unknown")
                sent_at = None if dry_run else datetime.now(UTC)
                await repo.record(
                    EmailDeliveryDTO(
                        edition_id=edition_id,
                        cadence=cadence,
                        email=str(entry.get("email", "")),
                        message_id=entry.get("message_id"),
                        relay_response=entry.get("status"),
                        outcome=outcome,
                        queued_at=queued_at,
                        sent_at=sent_at,
                    )
                )
            await session.commit()

    asyncio.run(_persist())
