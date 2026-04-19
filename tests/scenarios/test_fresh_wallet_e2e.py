"""Scenario 1 — Fresh wallet, Maduro-style.

Maps to `docs/newsletter-sections/01-fresh-wallets.md` case 1.1:
a newly created wallet placing a large bet on a niche market
hours before the event resolved.

Input: 3 TradeEvents against one market
  - `hot` wallet: fresh (nonce 2), age 2h, $30,000 BUY YES @ 0.05
  - `decoy-old`: 2-year-old wallet, $500 into the same market
  - `decoy-small`: fresh wallet, $200 (below fresh-wallet threshold)

Market: daily_volume $15k, category 'other' → niche market.

Expected assessments: one composite
  `fresh_wallet` + `niche_market` alert for the hot wallet.
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


MARKET_ID = "0xmaduro-leave-office-jan-2026"
MARKET_SLUG = "will-maduro-leave-office-by-end-of-jan-2026"
HOT_WALLET = "0x7a3f91"
DECOY_OLD = "0xdef789aa"
DECOY_SMALL = "0xbbbbcccc"

FRESH_WALLET_FIXTURE = Path(__file__).parent / "fixtures" / "inputs" / "fresh-wallet.jsonl"


def _trade(
    *, wallet: str, size: str, tx: str, price: str = "0.05"
) -> TradeEvent:
    return TradeEvent(
        market_id=MARKET_ID,
        trade_id=tx,
        wallet_address=wallet,
        side="BUY",
        outcome="Yes",
        outcome_index=0,
        price=Decimal(price),
        size=Decimal(size),
        timestamp=datetime(2026, 4, 19, 13, tzinfo=UTC),
        asset_id="0xasset-maduro-yes",
        market_slug=MARKET_SLUG,
        event_slug="maduro-jan-2026",
        event_title="Will Maduro leave office by end of Jan 2026?",
        trader_name="",
        trader_pseudonym="",
    )


@pytest.fixture
def scenario(tmp_path: Path, himalaya_binary: str) -> Scenario:
    return (
        Scenario(
            name="fresh-wallet-maduro",
            himalaya_binary=himalaya_binary,
            tmp_dir=tmp_path,
        )
        .given_trades(
            [
                # $30k BUY @ 0.05 → 600,000 shares → notional = $30,000
                _trade(wallet=HOT_WALLET, size="600000", tx="0xhot-tx"),
                # Decoy old: size chosen so notional = $250 (1.67% of
                # $15k daily volume → below the 2% size_anomaly
                # threshold). Old wallet so fresh_wallet won't fire.
                _trade(wallet=DECOY_OLD, size="5000", tx="0xdecoy-old-tx"),
                # Decoy small: fresh wallet but $200 notional — below
                # the $1,000 fresh-wallet min_trade_size AND below
                # the 2% size_anomaly threshold.
                _trade(wallet=DECOY_SMALL, size="4000", tx="0xdecoy-small-tx"),
            ]
        )
        .with_wallet_snapshots(
            {
                HOT_WALLET: WalletSnapshot(
                    address=HOT_WALLET,
                    nonce=2,
                    first_seen_at=datetime(2026, 4, 19, 11, tzinfo=UTC),
                    is_fresh=True,
                ),
                DECOY_OLD: WalletSnapshot(
                    address=DECOY_OLD,
                    nonce=847,
                    first_seen_at=datetime(2024, 3, 12, tzinfo=UTC),
                    is_fresh=False,
                ),
                DECOY_SMALL: WalletSnapshot(
                    address=DECOY_SMALL,
                    nonce=1,
                    first_seen_at=datetime(2026, 4, 19, 10, tzinfo=UTC),
                    is_fresh=True,
                ),
            }
        )
        .with_market_snapshots(
            {
                MARKET_ID: MarketSnapshot(
                    market_id=MARKET_ID,
                    daily_volume=Decimal("15000"),
                    book_depth=None,
                    category="other",
                ),
            }
        )
    )


# ---------------------------------------------------------------------------
# 2.2 Assertion tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fresh_wallet_emits_single_composite_alert(scenario: Scenario) -> None:
    """Task 2.2.1 — hot wallet triggers; both decoys filtered."""
    assessments = await scenario.when_replayed()
    assert len(assessments) == 1
    a = assessments[0]
    assert a.trade.wallet_address == HOT_WALLET
    # Both fresh-wallet and niche-market (and size-anomaly, because
    # $30k / $15k = 200% of daily volume) should compound.
    assert "fresh_wallet" in a.signals_triggered
    assert "niche_market" in a.signals_triggered
    assert "size_anomaly" in a.signals_triggered


@pytest.mark.asyncio
async def test_weighted_score_within_bounds(scenario: Scenario) -> None:
    """Task 2.2.2 — score in [0.5, 1.0], cap enforced."""
    assessments = await scenario.when_replayed()
    assert 0.5 <= assessments[0].weighted_score <= 1.0


@pytest.mark.asyncio
async def test_multi_signal_boost_applied(scenario: Scenario) -> None:
    """Task 2.2.3 — three signals => score reflects ×1.3 bonus, cap at 1.0."""
    assessments = await scenario.when_replayed()
    # 0.40 + 0.35 + 0.25 = 1.00 → × 1.2 (≥2 signals) × 1.3 (≥3 signals)
    # = 1.56 capped to 1.0.
    assert assessments[0].weighted_score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_alert_daily_rollup_populated(scenario: Scenario) -> None:
    """Task 2.2.4 — rollup row for fresh_wallet signal, 1 alert, $30k."""
    await scenario.when_replayed()
    rollup = scenario.aggregate_rollup(day=date(2026, 4, 19))
    key = ("2026-04-19", MARKET_ID, "fresh_wallet")
    assert key in rollup
    assert rollup[key]["alert_count"] == 1
    assert rollup[key]["unique_wallets"] == 1
    assert rollup[key]["total_notional"] == Decimal("30000")


@pytest.mark.asyncio
async def test_newsletter_renders_with_expected_substrings(
    scenario: Scenario,
) -> None:
    """Task 2.2.5 — daily newsletter body contains the hot-wallet cues."""
    assessments = await scenario.when_replayed()
    assert assessments, "scenario must produce at least one alert before rendering"
    # Build the minimal report payload the daily template expects.
    payload = {
        "date": "2026-04-19",
        "generated": "2026-04-19 13:00",
        "title": "Polymarket Snapshot — 2026-04-19",
        "stats": {
            "Top-N 24h volume total": "$1.2M",
            "Top-N liquidity total": "$600K",
        },
        "summary_markets": [
            {
                "Question": "Will Maduro leave office by end of Jan 2026?",
                "24h Vol": "$15K",
                "Liquidity": "$4.5K",
            }
        ],
        "sections": [
            {
                "title": "Top Markets by 24-Hour Volume",
                "market_count": 1,
                "markets": [],
            }
        ],
        "extra_sections": [],
        "observations": [
            "<strong>Thin book</strong>: Maduro market — $15K vol vs $4.5K liq (3x)",
        ],
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
            },
        },
    }
    template_path = (
        Path(__file__).resolve().parents[2]
        / "scripts" / "templates" / "polymarket-newsletter.html"
    )
    rendered = scenario.render_newsletter(
        template_path=template_path,
        report_payload=payload,
        subject="[AMI] Polymarket Snapshot — 2026-04-19",
    )
    # The harness returns a synthetic wrapper when himalaya succeeds
    # plus the full YAML data it sent in; assertions run against that.
    Scenario.assert_contains_all(
        rendered,
        [
            "status=dry-run",
            "Maduro leave office",
            "Thin book",
        ],
    )


# ---------------------------------------------------------------------------
# 2.4 Negative controls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutator,expected_count,rationale",
    [
        (
            lambda s: s.with_wallet_snapshots(
                {
                    HOT_WALLET: WalletSnapshot(
                        address=HOT_WALLET,
                        nonce=10,
                        first_seen_at=datetime(2026, 4, 19, 11, tzinfo=UTC),
                        is_fresh=False,
                    )
                }
            ),
            1,  # still fires: size_anomaly + niche_market remain
            "nonce=10 drops fresh_wallet but size + niche still trigger",
        ),
        (
            lambda s: s.given_trades([_trade(wallet=HOT_WALLET, size="15000", tx="0xtx-small", price="0.05")]),
            1,  # notional $750 < $1000 fresh threshold but still niche
            "$750 below fresh_wallet size threshold drops fresh signal",
        ),
    ],
)
async def test_boundary_mutations(
    scenario: Scenario,
    mutator,
    expected_count: int,
    rationale: str,
) -> None:
    """Task 2.4.1 — threshold boundary mutations behave as documented."""
    mutated = mutator(scenario)
    assessments = await mutated.when_replayed()
    assert len(assessments) == expected_count, (
        f"{rationale}: expected {expected_count} assessment(s), "
        f"got {len(assessments)}: "
        f"{[a.signals_triggered for a in assessments]}"
    )


@pytest.mark.asyncio
async def test_nonfresh_high_volume_market_does_not_alert(
    tmp_path: Path, himalaya_binary: str
) -> None:
    """Task 2.4.1 complement — decoy_old on high-volume market is silent."""
    scenario = (
        Scenario(
            name="fresh-wallet-negative",
            himalaya_binary=himalaya_binary,
            tmp_dir=tmp_path,
        )
        .given_trades(
            [
                _trade(wallet=DECOY_OLD, size="10000", tx="0xtx1"),
            ]
        )
        .with_wallet_snapshots(
            {
                DECOY_OLD: WalletSnapshot(
                    address=DECOY_OLD,
                    nonce=1000,
                    first_seen_at=datetime(2022, 1, 1, tzinfo=UTC),
                    is_fresh=False,
                )
            }
        )
        .with_market_snapshots(
            {
                MARKET_ID: MarketSnapshot(
                    market_id=MARKET_ID,
                    daily_volume=Decimal("100000000"),  # $100M
                    book_depth=None,
                    category="politics",
                )
            }
        )
    )
    assessments = await scenario.when_replayed()
    assert assessments == []


# ---------------------------------------------------------------------------
# 2.4.2 Parametric sweep over the (nonce × notional) detection region
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("nonce", [1, 2, 3, 4])
@pytest.mark.parametrize("notional_usdc", [1000, 5000, 30000, 100000])
async def test_fresh_wallet_fires_inside_region(
    tmp_path: Path, himalaya_binary: str, nonce: int, notional_usdc: int
) -> None:
    """Task 2.4.2 — for nonce ∈ [1..4] and notional ≥ $1k the detector fires."""
    size = int(notional_usdc / 0.05)
    scenario = (
        Scenario(
            name=f"sweep-{nonce}-{notional_usdc}",
            himalaya_binary=himalaya_binary,
            tmp_dir=tmp_path,
        )
        .given_trades(
            [_trade(wallet=HOT_WALLET, size=str(size), tx=f"0xtx-{nonce}-{notional_usdc}")]
        )
        .with_wallet_snapshots(
            {
                HOT_WALLET: WalletSnapshot(
                    address=HOT_WALLET,
                    nonce=nonce,
                    first_seen_at=datetime(2026, 4, 19, 11, tzinfo=UTC),
                    is_fresh=True,
                )
            }
        )
        # High-volume market so size_anomaly / niche don't mask the test.
        .with_market_snapshots(
            {
                MARKET_ID: MarketSnapshot(
                    market_id=MARKET_ID,
                    daily_volume=Decimal("100000000"),
                    book_depth=None,
                    category="politics",
                )
            }
        )
    )
    assessments = await scenario.when_replayed()
    assert len(assessments) == 1
    assert "fresh_wallet" in assessments[0].signals_triggered


@pytest.mark.asyncio
@pytest.mark.parametrize("nonce", [5, 10, 100])
async def test_fresh_wallet_silent_outside_region(
    tmp_path: Path, himalaya_binary: str, nonce: int
) -> None:
    """Task 2.4.2 — nonce ≥ 5 does not fire fresh_wallet."""
    scenario = (
        Scenario(
            name=f"sweep-silent-{nonce}",
            himalaya_binary=himalaya_binary,
            tmp_dir=tmp_path,
        )
        .given_trades([_trade(wallet=HOT_WALLET, size="600000", tx=f"0xtx-{nonce}")])
        .with_wallet_snapshots(
            {
                HOT_WALLET: WalletSnapshot(
                    address=HOT_WALLET,
                    nonce=nonce,
                    first_seen_at=datetime(2026, 4, 19, 11, tzinfo=UTC),
                    is_fresh=False,  # resolver says not fresh
                )
            }
        )
        .with_market_snapshots(
            {
                MARKET_ID: MarketSnapshot(
                    market_id=MARKET_ID,
                    daily_volume=Decimal("100000000"),
                    book_depth=None,
                    category="politics",
                )
            }
        )
    )
    assessments = await scenario.when_replayed()
    for a in assessments:
        assert "fresh_wallet" not in a.signals_triggered


# ---------------------------------------------------------------------------
# 2.3 Golden file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_matches_golden(
    scenario: Scenario, update_snapshots: bool
) -> None:
    """Task 2.3.1 — snapshot the rendered newsletter against a golden.

    On first run (or with --update-snapshots) the golden is written;
    thereafter the harness diffs scrubbed output against it.
    """
    await scenario.when_replayed()
    rollup = scenario.aggregate_rollup(day=date(2026, 4, 19))
    key = ("2026-04-19", MARKET_ID, "fresh_wallet")
    assert rollup[key]["alert_count"] == 1

    payload = {
        "date": "2026-04-19",
        "generated": "2026-04-19 13:00",
        "title": "Polymarket Snapshot — 2026-04-19",
        "stats": {
            "Fresh-wallet alerts": "1",
            "Total notional flagged": "$30,000",
        },
        "summary_markets": [
            {
                "Question": "Will Maduro leave office by end of Jan 2026?",
                "24h Vol": "$15K",
                "Liquidity": "$4.5K",
            }
        ],
        "sections": [{"title": "Top Markets", "market_count": 1, "markets": []}],
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
        subject="[AMI] Polymarket Snapshot — 2026-04-19",
    )
    golden = (
        Path(__file__).parent / "fixtures" / "golden" / "fresh-wallet-daily.html"
    )
    Scenario.assert_matches_golden(rendered, golden, update=update_snapshots)
