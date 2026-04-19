#!/usr/bin/env python3
"""Weekly hit-or-miss retrospective newsletter.

Scheduled Mondays 08:00 UTC via ami-cron. Reads:
- `detector_metrics` rows for the previous week (hit/miss/pending
  labels per signal).
- `alert_daily_rollup` rows for "top markets by alert density".
- `sniper_clusters` rows detected in the window.
- `wallet_profiles` + `funding_transfers` for the new-entity-funded
  callout.

Usage:
    uv run python scripts/weekly-newsletter.py                # last week
    uv run python scripts/weekly-newsletter.py --week-start 2026-04-14
    uv run python scripts/weekly-newsletter.py --dry-run
    uv run python scripts/weekly-newsletter.py --targets vlad
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import yaml
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from polymarket_insider_tracker.config import get_settings
from polymarket_insider_tracker.storage.repos import (
    AlertRollupRepository,
    DetectorMetricsRepository,
    SniperClusterRepository,
)

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_CONFIG = SCRIPT_DIR / "report-config.yaml"
TEMPLATE_PATH = SCRIPT_DIR / "templates" / "polymarket-weekly.html"


def _load_common():
    """Load the shared newsletter helpers (hyphenated filename -> importlib)."""
    spec = importlib.util.spec_from_file_location(
        "_newsletter_common", SCRIPT_DIR / "newsletter_common.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def fetch_report_payload(window_start: datetime, window_end: datetime) -> dict:
    """Pull all data the weekly template needs into a single dict."""
    settings = get_settings()
    engine = create_async_engine(settings.database.url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with factory() as session:
            metrics_repo = DetectorMetricsRepository(session)
            rollup_repo = AlertRollupRepository(session)
            sniper_repo = SniperClusterRepository(session)

            metrics = await metrics_repo.list_for_window(window_start, window_end)
            top_markets = await rollup_repo.top_markets_for_window(
                window_start.date(), window_end.date(), limit=5
            )
            clusters = await sniper_repo.list_since(window_start)
    finally:
        await engine.dispose()

    # Collapse metrics by signal for the summary table.
    metrics_by_signal: dict[str, dict] = {}
    for m in metrics:
        bucket = metrics_by_signal.setdefault(
            m.signal,
            {"alerts_total": 0, "hits": 0, "misses": 0, "pending": 0},
        )
        bucket["alerts_total"] += m.alerts_total
        bucket["hits"] += m.hits
        bucket["misses"] += m.misses
        bucket["pending"] += m.pending

    # Precision per signal = hits / (hits + misses) rounded to 4dp.
    def precision(row: dict) -> str:
        denom = row["hits"] + row["misses"]
        if denom == 0:
            return "—"
        return f"{row['hits'] / denom:.1%}"

    metrics_rows = [
        {
            "signal": signal,
            "alerts_total": bucket["alerts_total"],
            "hits": bucket["hits"],
            "misses": bucket["misses"],
            "pending": bucket["pending"],
            "precision": precision(bucket),
        }
        for signal, bucket in sorted(metrics_by_signal.items())
    ]

    top_markets_rows = [
        {"market_id": market_id, "alert_count": count}
        for market_id, count in top_markets
    ]

    cluster_rows = [
        {
            "cluster_id": c.cluster_id,
            "wallet_count": len(c.wallet_addresses),
            "avg_entry_delta_seconds": c.avg_entry_delta_seconds,
            "confidence": f"{c.confidence:.2f}" if c.confidence else "—",
            "markets_in_common": len(c.markets_in_common),
        }
        for c in clusters
    ]

    return {
        "window_start": window_start.date().isoformat(),
        "window_end": window_end.date().isoformat(),
        "generated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M"),
        "title": f"Polymarket Insider — weekly recap ({window_start.date().isoformat()} to {window_end.date().isoformat()})",
        "metrics_rows": metrics_rows,
        "top_markets_rows": top_markets_rows,
        "cluster_rows": cluster_rows,
    }


def build_rows(
    cfg: dict,
    targets: list[dict],
    report_payload: dict,
) -> list[dict]:
    """One YAML row per recipient."""
    email_cfg = cfg.get("email", {})
    default_subject_tpl = email_cfg.get(
        "weekly_subject_template",
        "[AMI] Polymarket — Weekly Recap {week_end}",
    )
    rows: list[dict] = []
    for target in targets:
        subject_tpl = target.get("subject_template", default_subject_tpl)
        subject = subject_tpl.format(
            week_start=report_payload["window_start"],
            week_end=report_payload["window_end"],
        )
        rows.append({
            "email": target["email"],
            "name": target.get("name", target["email"]),
            "subject": subject,
            "report": report_payload,
        })
    return rows


async def main_async(args: argparse.Namespace) -> int:
    cfg = yaml.safe_load(args.config.read_text())
    if args.week_start:
        start = datetime.combine(args.week_start, time.min, tzinfo=UTC)
    else:
        today = datetime.now(UTC).date()
        # Monday of last week = this Monday minus 7 days.
        monday_this_week = today - timedelta(days=today.weekday())
        start = datetime.combine(monday_this_week - timedelta(days=7), time.min, tzinfo=UTC)
    end = start + timedelta(days=7)

    print(f"[1/2] Aggregating data for {start.date()} to {end.date()}...")
    payload = await fetch_report_payload(start, end)

    if args.no_send:
        print("[SKIP] --no-send flag set, skipping email delivery")
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
    parser = argparse.ArgumentParser(description="Polymarket weekly recap")
    parser.add_argument("--config", "-c", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--week-start",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="UTC Monday at the start of the window (default: last full week)",
    )
    parser.add_argument("--no-send", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--targets", help="Comma-separated target names")
    args = parser.parse_args(argv)

    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
