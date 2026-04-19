#!/usr/bin/env python3
"""Monthly calibration dashboard newsletter.

Scheduled on the 1st of each month at 09:00 UTC via ami-cron. Reads
primarily from `detector_metrics` (populated by the backtest replay)
and secondarily from `sniper_clusters` for the long-tail view.

Usage:
    uv run python scripts/monthly-newsletter.py                # last month
    uv run python scripts/monthly-newsletter.py --month 2026-03
    uv run python scripts/monthly-newsletter.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import calendar
import importlib.util
import sys
from collections import defaultdict
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import yaml
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from polymarket_insider_tracker.config import get_settings
from polymarket_insider_tracker.storage.repos import (
    DetectorMetricsRepository,
    SniperClusterRepository,
)

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "report-config.yaml"
TEMPLATE_PATH = SCRIPT_DIR / "templates" / "polymarket-monthly.html"


def _load_common():
    spec = importlib.util.spec_from_file_location(
        "_newsletter_common", SCRIPT_DIR / "newsletter_common.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _parse_month(raw: str) -> tuple[datetime, datetime]:
    """'2026-03' → (start_of_month, start_of_next_month)."""
    year_str, month_str = raw.split("-", 1)
    year = int(year_str)
    month = int(month_str)
    start = datetime(year, month, 1, tzinfo=UTC)
    _, last_day = calendar.monthrange(year, month)
    end = start + timedelta(days=last_day)
    return start, end


def _previous_month(now: datetime) -> tuple[datetime, datetime]:
    first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_of_prev = first_of_this_month - timedelta(days=1)
    return _parse_month(f"{last_of_prev.year}-{last_of_prev.month:02d}")


async def fetch_report_payload(window_start: datetime, window_end: datetime) -> dict:
    settings = get_settings()
    engine = create_async_engine(settings.database.url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with factory() as session:
            metrics_repo = DetectorMetricsRepository(session)
            sniper_repo = SniperClusterRepository(session)
            metrics = await metrics_repo.list_for_window(window_start, window_end)
            clusters = await sniper_repo.list_since(window_start)
    finally:
        await engine.dispose()

    # Per-signal roll-up across the month.
    per_signal: dict[str, dict[str, int]] = defaultdict(
        lambda: {"alerts_total": 0, "hits": 0, "misses": 0, "pending": 0, "uplift_sum": 0, "uplift_n": 0}
    )
    for m in metrics:
        per_signal[m.signal]["alerts_total"] += m.alerts_total
        per_signal[m.signal]["hits"] += m.hits
        per_signal[m.signal]["misses"] += m.misses
        per_signal[m.signal]["pending"] += m.pending
        if m.pnl_uplift_bps is not None:
            per_signal[m.signal]["uplift_sum"] += m.pnl_uplift_bps
            per_signal[m.signal]["uplift_n"] += 1

    def precision(row: dict) -> str:
        denom = row["hits"] + row["misses"]
        return f"{row['hits'] / denom:.1%}" if denom else "—"

    def uplift(row: dict) -> str:
        return f"{row['uplift_sum'] // row['uplift_n']} bps" if row["uplift_n"] else "—"

    signal_rows = [
        {
            "signal": signal,
            "alerts_total": row["alerts_total"],
            "hits": row["hits"],
            "misses": row["misses"],
            "pending": row["pending"],
            "precision": precision(row),
            "pnl_uplift_bps": uplift(row),
        }
        for signal, row in sorted(per_signal.items())
    ]

    # Sniper wallets that appeared in ≥ 3 distinct clusters during the month.
    wallet_appearances: dict[str, int] = defaultdict(int)
    for c in clusters:
        for wallet in c.wallet_addresses:
            wallet_appearances[wallet] += 1
    recidivist_wallets = [
        {"wallet_address": addr, "clusters": count}
        for addr, count in sorted(
            wallet_appearances.items(), key=lambda kv: kv[1], reverse=True
        )
        if count >= 3
    ][:10]

    return {
        "window_start": window_start.date().isoformat(),
        "window_end": window_end.date().isoformat(),
        "generated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M"),
        "title": f"Polymarket Insider — calibration dashboard ({window_start:%B %Y})",
        "signal_rows": signal_rows,
        "recidivist_wallets": recidivist_wallets,
        "cluster_count": len(clusters),
    }


def build_rows(cfg: dict, targets: list[dict], payload: dict) -> list[dict]:
    email_cfg = cfg.get("email", {})
    default_subject_tpl = email_cfg.get(
        "monthly_subject_template",
        "[AMI] Polymarket — Calibration Dashboard {month}",
    )
    rows: list[dict] = []
    for target in targets:
        subject_tpl = target.get("subject_template", default_subject_tpl)
        subject = subject_tpl.format(month=payload["title"].split("(")[-1].rstrip(")"))
        rows.append({
            "email": target["email"],
            "name": target.get("name", target["email"]),
            "subject": subject,
            "report": payload,
        })
    return rows


async def main_async(args: argparse.Namespace) -> int:
    cfg = yaml.safe_load(args.config.read_text())
    start, end = (
        _parse_month(args.month) if args.month else _previous_month(datetime.now(UTC))
    )

    print(f"[1/2] Aggregating metrics for {start.date()} to {end.date()}...")
    payload = await fetch_report_payload(start, end)

    if args.no_send:
        print("[SKIP] --no-send; payload:")
        print(yaml.safe_dump({"payload": payload}, sort_keys=False))
        return 0

    print("[2/2] Delivering...")
    common = _load_common()
    all_targets = cfg.get("delivery", {}).get("targets", [])
    targets = common.filter_targets(all_targets, args.targets)
    rows = build_rows(cfg, targets, payload)
    return common.deliver_via_himalaya(
        rows=rows,
        template_path=TEMPLATE_PATH,
        subject_template="{{ subject }}",
        account=cfg.get("delivery", {}).get("account", "polymarket"),
        rate=cfg.get("delivery", {}).get("rate", "5/min"),
        dry_run=args.dry_run,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Polymarket monthly calibration dashboard")
    parser.add_argument("--config", "-c", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--month",
        default=None,
        help="YYYY-MM string for the month to report on (default: last month)",
    )
    parser.add_argument("--no-send", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--targets", help="Comma-separated target names")
    args = parser.parse_args(argv)

    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
