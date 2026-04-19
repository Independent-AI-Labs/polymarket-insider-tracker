"""Task 12.6 — weekly/monthly newsletters degrade gracefully on empty data.

Disable the backtest capture for a hypothetical 24h window → the
`detector_metrics` / `sniper_clusters` / `alert_daily_rollup` tables
are empty or sparse. The Tera templates must render without errors
and without misleading "0 alerts = perfect precision" copy.

The daily newsletter already has an existence check in the data
builder (it falls through to the market snapshot). The weekly /
monthly templates need an empty-state string so readers know the
newsletter isn't silently broken.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.scenarios._harness import Scenario


@pytest.mark.asyncio
async def test_weekly_template_renders_with_empty_metrics(
    tmp_path: Path, himalaya_binary: str
) -> None:
    scenario = Scenario(
        name="graceful-degrade-weekly",
        himalaya_binary=himalaya_binary,
        tmp_dir=tmp_path,
    )
    payload = {
        "window_start": "2026-04-13",
        "window_end": "2026-04-20",
        "generated": "2026-04-21 08:00",
        "title": "Polymarket Insider — weekly recap (empty window)",
        "metrics_rows": [],       # capture was disabled
        "top_markets_rows": [],   # rollup produced no rows
        "cluster_rows": [],       # no sniper clusters detected
    }
    template_path = (
        Path(__file__).resolve().parents[2]
        / "scripts" / "templates" / "polymarket-weekly.html"
    )
    rendered = scenario.render_newsletter(
        template_path=template_path,
        report_payload=payload,
        subject="[AMI] Polymarket — Weekly Recap (empty)",
    )
    # himalaya must accept the template (status=dry-run) even when
    # every data list is empty — no null-pointer surprises.
    Scenario.assert_contains_all(rendered, ["status=dry-run"])


@pytest.mark.asyncio
async def test_monthly_template_renders_with_empty_metrics(
    tmp_path: Path, himalaya_binary: str
) -> None:
    scenario = Scenario(
        name="graceful-degrade-monthly",
        himalaya_binary=himalaya_binary,
        tmp_dir=tmp_path,
    )
    payload = {
        "window_start": "2026-04-01",
        "window_end": "2026-05-01",
        "generated": "2026-05-01 09:00",
        "title": "Polymarket Insider — calibration dashboard (April 2026)",
        "signal_rows": [],
        "recidivist_wallets": [],
        "cluster_count": 0,
    }
    template_path = (
        Path(__file__).resolve().parents[2]
        / "scripts" / "templates" / "polymarket-monthly.html"
    )
    rendered = scenario.render_newsletter(
        template_path=template_path,
        report_payload=payload,
        subject="[AMI] Polymarket — Calibration Dashboard (empty)",
    )
    Scenario.assert_contains_all(rendered, ["status=dry-run"])


@pytest.mark.asyncio
async def test_daily_template_renders_with_empty_observations(
    tmp_path: Path, himalaya_binary: str
) -> None:
    scenario = Scenario(
        name="graceful-degrade-daily",
        himalaya_binary=himalaya_binary,
        tmp_dir=tmp_path,
    )
    payload = {
        "date": "2026-04-19",
        "generated": "2026-04-19 13:00",
        "title": "Polymarket Snapshot — 2026-04-19",
        "stats": {},
        "summary_markets": [],
        "sections": [],
        "extra_sections": [],
        "observations": [],
        "config": {
            "email": {
                "summary_top_n": 5,
                "max_observations": 5,
                "footer_text": "AMI Reports",
                "style": {
                    "font_family": "sans-serif",
                    "max_width": "640px",
                    "header_bg": "#1a1a2e",
                    "header_text": "#ffffff",
                    "accent_color": "#aaaaaa",
                },
            }
        },
    }
    template_path = (
        Path(__file__).resolve().parents[2]
        / "scripts" / "templates" / "polymarket-newsletter.html"
    )
    rendered = scenario.render_newsletter(
        template_path=template_path,
        report_payload=payload,
        subject="[AMI] Polymarket Snapshot (empty)",
    )
    Scenario.assert_contains_all(rendered, ["status=dry-run"])
