"""Scenario 2 — Unusual sizing, Iran-strike-style.

Maps to `docs/newsletter-sections/02-unusual-sizing.md`: a large
notional against a liquid market with enough book depth that the
trade consumes a disproportionate share (≥ 2% of 24h volume
AND ≥ 5% of order-book depth).

Input: 2 TradeEvents against one mainstream market
  - `hot`:   $48k BUY NO @ 0.28   (7.1% of $680k daily volume,
                                    24% of $200k book depth)
  - `decoy`: $10k BUY YES @ 0.28  (1.5% of volume → silent)

Both wallets are old/senior so fresh_wallet never fires. Market
is `politics`, $680k daily → niche_market never fires either.
Only `size_anomaly` should trigger, and only for the hot trade.
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


MARKET_ID = "0xiran-operation-by-friday"
MARKET_SLUG = "will-operation-x-begin-by-friday"
HOT_WALLET = "0xdef7890abcdef"
DECOY_WALLET = "0xcafecafecafecafecafe"


def _trade(
    *, wallet: str, notional_usdc: int, price: str = "0.28",
    tx: str, side: str = "BUY", outcome_idx: int = 1,
) -> TradeEvent:
    size = Decimal(notional_usdc) / Decimal(price)
    return TradeEvent(
        market_id=MARKET_ID,
        trade_id=tx,
        wallet_address=wallet,
        side=side,  # type: ignore[arg-type]
        outcome="No" if outcome_idx == 1 else "Yes",
        outcome_index=outcome_idx,
        price=Decimal(price),
        size=size,
        timestamp=datetime(2026, 4, 19, 13, tzinfo=UTC),
        asset_id=f"0xasset-{outcome_idx}",
        market_slug=MARKET_SLUG,
        event_slug="operation-x-friday",
        event_title="Will Operation X begin by Friday?",
        trader_name="",
        trader_pseudonym="",
    )


@pytest.fixture
def scenario(tmp_path: Path, himalaya_binary: str) -> Scenario:
    return (
        Scenario(
            name="unusual-sizing-iran",
            himalaya_binary=himalaya_binary,
            tmp_dir=tmp_path,
        )
        .given_trades(
            [
                _trade(wallet=HOT_WALLET, notional_usdc=48000, tx="0xhot-tx"),
                _trade(wallet=DECOY_WALLET, notional_usdc=10000, tx="0xdecoy-tx"),
            ]
        )
        .with_wallet_snapshots(
            {
                HOT_WALLET: WalletSnapshot(
                    address=HOT_WALLET, nonce=500, first_seen_at=None, is_fresh=False
                ),
                DECOY_WALLET: WalletSnapshot(
                    address=DECOY_WALLET,
                    nonce=1200,
                    first_seen_at=None,
                    is_fresh=False,
                ),
            }
        )
        .with_market_snapshots(
            {
                MARKET_ID: MarketSnapshot(
                    market_id=MARKET_ID,
                    daily_volume=Decimal("680000"),
                    book_depth=Decimal("200000"),
                    category="politics",
                ),
            }
        )
    )


@pytest.mark.asyncio
async def test_hot_trade_fires_size_anomaly_only(scenario: Scenario) -> None:
    """Task 3.2.1 — hot trade emits exactly size_anomaly (no fresh, no niche)."""
    assessments = await scenario.when_replayed()
    hot = [a for a in assessments if a.trade.wallet_address == HOT_WALLET]
    assert len(hot) == 1
    assert hot[0].signals_triggered == ("size_anomaly",)


@pytest.mark.asyncio
async def test_decoy_trade_silent(scenario: Scenario) -> None:
    """Task 3.4.1 — decoy at 1.5% of volume stays below the 2% threshold."""
    assessments = await scenario.when_replayed()
    decoys = [a for a in assessments if a.trade.wallet_address == DECOY_WALLET]
    assert decoys == []


@pytest.mark.asyncio
async def test_detector_metrics_aggregate(scenario: Scenario) -> None:
    """Task 3.2.3 — aggregating the replay window yields the expected row."""
    from polymarket_insider_tracker.backtest.metrics import (
        COMBINED_SIGNAL, MetricsWindow, aggregate_metrics,
    )
    from polymarket_insider_tracker.backtest.outcomes import (
        MarketOutcome, OutcomeLabel, classify_assessment,
    )

    await scenario.when_replayed()
    # Seed a market outcome so classification has something to decide.
    outcome = MarketOutcome(
        market_id=MARKET_ID,
        reference_price=Decimal("0.28"),
        final_price=Decimal("1.00"),
        is_resolved=True,
    )
    outcomes = [
        classify_assessment(
            assessment_id=a.assessment_id,
            wallet_address=a.trade.wallet_address,
            market_id=a.trade.market_id,
            side=a.trade.side,
            outcome_index=a.trade.outcome_index,
            signals_triggered=a.signals_triggered,
            weighted_score=a.weighted_score,
            outcome=outcome,
        )
        for a in scenario._assessments
    ]
    window = MetricsWindow(
        start=datetime(2026, 4, 19, tzinfo=UTC),
        end=datetime(2026, 4, 20, tzinfo=UTC),
    )
    rows = aggregate_metrics(outcomes, window)
    size_row = next(r for r in rows if r.signal == "size_anomaly")
    assert size_row.alerts_total == 1
    combined = next(r for r in rows if r.signal == COMBINED_SIGNAL)
    assert combined.alerts_total == 1
    # Hot trade was BUY NO @ 0.28; if market resolved YES (final=1.0),
    # the signaled side MOVED AGAINST the bet. Contract was outcome=NO
    # so the side price went 0.28 → 0.00 effectively. We're just
    # checking the aggregator here — outcome is one deterministic
    # label per trade.
    assert size_row.hits + size_row.misses + size_row.pending == 1


@pytest.mark.asyncio
async def test_rollup_grouping(scenario: Scenario) -> None:
    """Task 3.2.3 complement — alert_daily_rollup reflects the single alert."""
    await scenario.when_replayed()
    rollup = scenario.aggregate_rollup(day=date(2026, 4, 19))
    key = ("2026-04-19", MARKET_ID, "size_anomaly")
    assert key in rollup
    assert rollup[key]["alert_count"] == 1
    assert rollup[key]["unique_wallets"] == 1
    # Notional reconstructed from size × price — rounding through the
    # Decimal division leaves a sub-cent remainder. Tolerance: $1.
    assert abs(rollup[key]["total_notional"] - Decimal("48000")) < 1


# ---------------------------------------------------------------------------
# 3.4 Negative controls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scaled_up_volume_silences_alert(
    tmp_path: Path, himalaya_binary: str
) -> None:
    """Task 3.4.1 — bumping daily_volume 10× drops the alert."""
    scenario = (
        Scenario(
            name="unusual-sizing-silent",
            himalaya_binary=himalaya_binary,
            tmp_dir=tmp_path,
        )
        .given_trades(
            [_trade(wallet=HOT_WALLET, notional_usdc=48000, tx="0xhot-tx")]
        )
        .with_wallet_snapshots(
            {
                HOT_WALLET: WalletSnapshot(
                    address=HOT_WALLET, nonce=500, first_seen_at=None, is_fresh=False
                ),
            }
        )
        .with_market_snapshots(
            {
                MARKET_ID: MarketSnapshot(
                    market_id=MARKET_ID,
                    daily_volume=Decimal("6800000"),  # 10× higher
                    book_depth=Decimal("200000"),
                    category="politics",
                ),
            }
        )
    )
    assessments = await scenario.when_replayed()
    assert assessments == []


@pytest.mark.asyncio
async def test_missing_book_depth_falls_back_to_volume(
    tmp_path: Path, himalaya_binary: str
) -> None:
    """Task 3.4.2 — detector fires via volume-impact even without book_depth."""
    scenario = (
        Scenario(
            name="unusual-sizing-no-depth",
            himalaya_binary=himalaya_binary,
            tmp_dir=tmp_path,
        )
        .given_trades(
            [_trade(wallet=HOT_WALLET, notional_usdc=48000, tx="0xtx")]
        )
        .with_wallet_snapshots(
            {
                HOT_WALLET: WalletSnapshot(
                    address=HOT_WALLET, nonce=500, first_seen_at=None, is_fresh=False
                ),
            }
        )
        .with_market_snapshots(
            {
                MARKET_ID: MarketSnapshot(
                    market_id=MARKET_ID,
                    daily_volume=Decimal("680000"),
                    book_depth=None,
                    category="politics",
                ),
            }
        )
    )
    assessments = await scenario.when_replayed()
    assert len(assessments) == 1
    assert assessments[0].signals_triggered == ("size_anomaly",)


@pytest.mark.asyncio
async def test_both_volume_and_depth_absent_silent(
    tmp_path: Path, himalaya_binary: str
) -> None:
    """Task 3.4.3 — no metadata at all: detector can't evaluate, stays silent."""
    scenario = (
        Scenario(
            name="unusual-sizing-blind",
            himalaya_binary=himalaya_binary,
            tmp_dir=tmp_path,
        )
        .given_trades(
            [_trade(wallet=HOT_WALLET, notional_usdc=48000, tx="0xtx")]
        )
        .with_wallet_snapshots(
            {
                HOT_WALLET: WalletSnapshot(
                    address=HOT_WALLET, nonce=500, first_seen_at=None, is_fresh=False
                ),
            }
        )
        .with_market_snapshots(
            {
                MARKET_ID: MarketSnapshot(
                    market_id=MARKET_ID,
                    daily_volume=None,
                    book_depth=None,
                    category="politics",
                ),
            }
        )
    )
    assessments = await scenario.when_replayed()
    assert assessments == []


# ---------------------------------------------------------------------------
# 3.3 Golden file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_matches_golden(
    scenario: Scenario, update_snapshots: bool
) -> None:
    await scenario.when_replayed()
    payload = {
        "date": "2026-04-19",
        "generated": "2026-04-19 13:00",
        "title": "Polymarket — High-conviction positions",
        "stats": {"Size-anomaly alerts": "1"},
        "summary_markets": [
            {
                "Question": "Will Operation X begin by Friday?",
                "24h Vol": "$680K",
                "Liquidity": "$200K",
            }
        ],
        "sections": [
            {"title": "Top Markets by 24-Hour Volume", "market_count": 1, "markets": []}
        ],
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
        subject="[AMI] Polymarket — High-conviction 2026-04-19",
    )
    golden = (
        Path(__file__).parent / "fixtures" / "golden" / "unusual-sizing-daily.html"
    )
    Scenario.assert_matches_golden(rendered, golden, update=update_snapshots)
