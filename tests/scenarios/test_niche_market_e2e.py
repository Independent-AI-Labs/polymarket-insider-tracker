"""Scenario 3 — Niche market, Google-search-style.

Maps to `docs/newsletter-sections/03-niche-markets.md`: wallets
target a low-volume (< $50k daily) market in a
niche-prone category (`other`, `science`, `tech`, `finance`). This
is the first scenario that drives the **weekly** template instead of
the daily one.

Input: 2 trades into a market with daily_volume=$25k
  - both exceed 2% (size_anomaly) AND land in a niche category
    (niche_market) → composite 2-signal alerts.
  - wallets are mature (nonce 200+) so fresh_wallet stays silent.

Expected: 2 `niche_market + size_anomaly` assessments → 2 rollup rows.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_insider_tracker.backtest.replay import (
    MarketSnapshot,
    WalletSnapshot,
)
from polymarket_insider_tracker.ingestor.models import TradeEvent
from tests.scenarios._harness import Scenario


MARKET_ID = "0xgoogle-year-in-search-2025-rank-1"
MARKET_SLUG = "google-year-in-search-2025-rank-1"
WALLET_A = "0xafEe0000000000000000000000000000000000aa"
WALLET_B = "0xafEe0000000000000000000000000000000000bb"


def _trade(
    *, wallet: str, notional_usdc: int, tx: str, price: str = "0.12"
) -> TradeEvent:
    size = Decimal(notional_usdc) / Decimal(price)
    return TradeEvent(
        market_id=MARKET_ID,
        trade_id=tx,
        wallet_address=wallet,
        side="BUY",
        outcome="Yes",
        outcome_index=0,
        price=Decimal(price),
        size=size,
        timestamp=datetime(2026, 4, 19, 14, tzinfo=UTC),
        asset_id="0xasset-yes",
        market_slug=MARKET_SLUG,
        event_slug="google-year-in-search-2025",
        event_title="Will <trend> be #1 on Google Year in Search 2025?",
        trader_name="AlphaRaccoon",
        trader_pseudonym="",
    )


@pytest.fixture
def scenario(tmp_path: Path, himalaya_binary: str) -> Scenario:
    return (
        Scenario(
            name="niche-market-google",
            himalaya_binary=himalaya_binary,
            tmp_dir=tmp_path,
        )
        .given_trades(
            [
                _trade(wallet=WALLET_A, notional_usdc=2500, tx="0xtx-a"),
                _trade(wallet=WALLET_B, notional_usdc=4200, tx="0xtx-b"),
            ]
        )
        .with_wallet_snapshots(
            {
                WALLET_A: WalletSnapshot(
                    address=WALLET_A, nonce=250, first_seen_at=None, is_fresh=False
                ),
                WALLET_B: WalletSnapshot(
                    address=WALLET_B, nonce=810, first_seen_at=None, is_fresh=False
                ),
            }
        )
        .with_market_snapshots(
            {
                MARKET_ID: MarketSnapshot(
                    market_id=MARKET_ID,
                    daily_volume=Decimal("25000"),
                    book_depth=None,
                    category="other",
                )
            }
        )
    )


@pytest.mark.asyncio
async def test_both_trades_fire_niche_plus_size_anomaly(scenario: Scenario) -> None:
    """Task 4.2.1 — both assessments carry (size_anomaly, niche_market)."""
    assessments = await scenario.when_replayed()
    assert len(assessments) == 2
    for a in assessments:
        assert "niche_market" in a.signals_triggered
        assert "size_anomaly" in a.signals_triggered
        assert "fresh_wallet" not in a.signals_triggered


@pytest.mark.asyncio
async def test_multi_signal_boost(scenario: Scenario) -> None:
    """Task 4.2.2 — 2 signals → 0.35 + 0.25 = 0.60 × 1.2 = 0.72."""
    assessments = await scenario.when_replayed()
    for a in assessments:
        assert a.weighted_score == pytest.approx(0.72, abs=0.01)


@pytest.mark.asyncio
async def test_rollup_groups_by_signal(scenario: Scenario) -> None:
    """Task 4.2.3 — one rollup row per (market, signal) combination."""
    await scenario.when_replayed()
    rollup = scenario.aggregate_rollup(day=date(2026, 4, 19))
    niche_key = ("2026-04-19", MARKET_ID, "niche_market")
    size_key = ("2026-04-19", MARKET_ID, "size_anomaly")
    assert niche_key in rollup
    assert size_key in rollup
    assert rollup[niche_key]["alert_count"] == 2
    assert rollup[niche_key]["unique_wallets"] == 2


@pytest.mark.asyncio
async def test_weekly_template_renders(scenario: Scenario) -> None:
    """Task 4.2.4 / 4.2.5 — weekly builder drives the weekly Tera template."""
    await scenario.when_replayed()
    weekly_payload = {
        "window_start": "2026-04-13",
        "window_end": "2026-04-20",
        "generated": "2026-04-21 08:00",
        "title": "Polymarket Insider — weekly recap (2026-04-13 to 2026-04-20)",
        "metrics_rows": [
            {
                "signal": "niche_market",
                "alerts_total": 2,
                "hits": 1,
                "misses": 0,
                "pending": 1,
                "precision": "100.0%",
            }
        ],
        "top_markets_rows": [
            {"market_id": MARKET_ID, "alert_count": 2},
        ],
        "cluster_rows": [],
    }
    template_path = (
        Path(__file__).resolve().parents[2]
        / "scripts" / "templates" / "polymarket-weekly.html"
    )
    rendered = scenario.render_newsletter(
        template_path=template_path,
        report_payload=weekly_payload,
        subject="[AMI] Polymarket — Weekly Recap 2026-04-20",
    )
    Scenario.assert_contains_all(
        rendered,
        [
            "status=dry-run",
            "niche_market",
            MARKET_ID,
            "weekly recap",
        ],
    )


# ---------------------------------------------------------------------------
# 4.4 Negative controls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_high_volume_market_drops_niche_signal(
    tmp_path: Path, himalaya_binary: str
) -> None:
    """Task 4.4.1 — daily_volume = $100k → niche_market silent."""
    scenario = (
        Scenario(
            name="niche-market-wider",
            himalaya_binary=himalaya_binary,
            tmp_dir=tmp_path,
        )
        .given_trades([_trade(wallet=WALLET_A, notional_usdc=3000, tx="0xtx")])
        .with_wallet_snapshots(
            {
                WALLET_A: WalletSnapshot(
                    address=WALLET_A, nonce=250, first_seen_at=None, is_fresh=False
                )
            }
        )
        .with_market_snapshots(
            {
                MARKET_ID: MarketSnapshot(
                    market_id=MARKET_ID,
                    daily_volume=Decimal("100000"),
                    book_depth=None,
                    category="other",
                )
            }
        )
    )
    assessments = await scenario.when_replayed()
    for a in assessments:
        assert "niche_market" not in a.signals_triggered


@pytest.mark.asyncio
async def test_mainstream_category_drops_niche_signal(
    tmp_path: Path, himalaya_binary: str
) -> None:
    """Task 4.4.2 — `politics` category isn't niche-prone; niche silent."""
    scenario = (
        Scenario(
            name="niche-market-politics",
            himalaya_binary=himalaya_binary,
            tmp_dir=tmp_path,
        )
        .given_trades([_trade(wallet=WALLET_A, notional_usdc=3000, tx="0xtx")])
        .with_wallet_snapshots(
            {
                WALLET_A: WalletSnapshot(
                    address=WALLET_A, nonce=250, first_seen_at=None, is_fresh=False
                )
            }
        )
        .with_market_snapshots(
            {
                MARKET_ID: MarketSnapshot(
                    market_id=MARKET_ID,
                    daily_volume=Decimal("25000"),
                    book_depth=None,
                    category="politics",
                )
            }
        )
    )
    assessments = await scenario.when_replayed()
    for a in assessments:
        assert "niche_market" not in a.signals_triggered
        # size_anomaly still fires (3000 / 25000 = 12%)
        assert "size_anomaly" in a.signals_triggered


# ---------------------------------------------------------------------------
# 4.3 Golden file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_matches_golden(
    scenario: Scenario, update_snapshots: bool
) -> None:
    await scenario.when_replayed()
    weekly_payload = {
        "window_start": "2026-04-13",
        "window_end": "2026-04-20",
        "generated": "2026-04-21 08:00",
        "title": "Polymarket Insider — weekly recap (2026-04-13 to 2026-04-20)",
        "metrics_rows": [
            {
                "signal": "niche_market",
                "alerts_total": 2,
                "hits": 1,
                "misses": 0,
                "pending": 1,
                "precision": "100.0%",
            }
        ],
        "top_markets_rows": [
            {"market_id": MARKET_ID, "alert_count": 2},
        ],
        "cluster_rows": [],
    }
    template_path = (
        Path(__file__).resolve().parents[2]
        / "scripts" / "templates" / "polymarket-weekly.html"
    )
    rendered = scenario.render_newsletter(
        template_path=template_path,
        report_payload=weekly_payload,
        subject="[AMI] Polymarket — Weekly Recap 2026-04-20",
    )
    golden = (
        Path(__file__).parent / "fixtures" / "golden" / "niche-market-weekly.html"
    )
    Scenario.assert_matches_golden(rendered, golden, update=update_snapshots)
