"""Task 12.7 — internal consistency of a single combined newsletter edition.

All 4 sections (fresh wallets / unusual sizing / niche markets /
funding clusters) can share data sources. A wallet flagged in
Section 1 and again in Section 3 should appear with the same
address, the same notional, and the same market. The daily
newsletter doesn't currently combine all four — but the weekly
retrospective does, and this test guards against drift.

The check is structural: we produce the weekly payload from a known
set of assessments + cluster rows and confirm the per-section
wallet lists don't contradict each other.
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


MARKET_A = "0xcombined-market-a"
MARKET_B = "0xcombined-market-b"
WALLET_SHARED = "0xcombined000000000000000000000000000000a0"
WALLET_SOLO = "0xcombined000000000000000000000000000000b0"


def _trade(*, wallet: str, market: str, tx: str) -> TradeEvent:
    return TradeEvent(
        market_id=market,
        trade_id=tx,
        wallet_address=wallet,
        side="BUY",
        outcome="Yes",
        outcome_index=0,
        price=Decimal("0.1"),
        size=Decimal("100000"),  # notional $10k
        timestamp=datetime(2026, 4, 19, 13, tzinfo=UTC),
        asset_id="0xasset",
        market_slug="mkt",
        event_slug="evt",
        event_title="Event",
        trader_name="",
        trader_pseudonym="",
    )


@pytest.mark.asyncio
async def test_wallet_appearing_in_multiple_sections_is_consistent(
    tmp_path: Path, himalaya_binary: str
) -> None:
    """WALLET_SHARED trades into two markets → shows up in the
    fresh-wallet list AND in the niche-market top-markets list with
    the same address. Counts match across sections."""
    scenario = (
        Scenario(
            name="combined-consistency",
            himalaya_binary=himalaya_binary,
            tmp_dir=tmp_path,
        )
        .given_trades(
            [
                _trade(wallet=WALLET_SHARED, market=MARKET_A, tx="0xtx-a1"),
                _trade(wallet=WALLET_SHARED, market=MARKET_B, tx="0xtx-b1"),
                _trade(wallet=WALLET_SOLO, market=MARKET_A, tx="0xtx-a2"),
            ]
        )
        .with_wallet_snapshots(
            {
                w: WalletSnapshot(
                    address=w, nonce=2, first_seen_at=None, is_fresh=True
                )
                for w in (WALLET_SHARED, WALLET_SOLO)
            }
        )
        .with_market_snapshots(
            {
                MARKET_A: MarketSnapshot(
                    market_id=MARKET_A,
                    daily_volume=Decimal("40000"),
                    book_depth=None,
                    category="other",
                ),
                MARKET_B: MarketSnapshot(
                    market_id=MARKET_B,
                    daily_volume=Decimal("40000"),
                    book_depth=None,
                    category="other",
                ),
            }
        )
    )
    assessments = await scenario.when_replayed()
    assert len(assessments) == 3

    # Section 1 (fresh wallets): WALLET_SHARED appears twice (once
    # per market); WALLET_SOLO once. Deduped across markets we see
    # 2 unique wallets total in Section 1.
    fresh_assessments = [a for a in assessments if "fresh_wallet" in a.signals_triggered]
    unique_fresh_wallets = {a.trade.wallet_address for a in fresh_assessments}
    assert unique_fresh_wallets == {WALLET_SHARED, WALLET_SOLO}

    # Section 3 (niche markets): both markets flagged; WALLET_SHARED
    # participates in both, WALLET_SOLO only in MARKET_A.
    rollup = scenario.aggregate_rollup(day=date(2026, 4, 19))
    niche_a = rollup[("2026-04-19", MARKET_A, "niche_market")]
    niche_b = rollup[("2026-04-19", MARKET_B, "niche_market")]
    assert niche_a["unique_wallets"] == 2
    assert niche_b["unique_wallets"] == 1  # only WALLET_SHARED
    # Notional consistency: Section 1 totals equal Section 3 totals.
    section1_notional = sum(
        a.trade.price * a.trade.size for a in fresh_assessments
    )
    section3_notional = niche_a["total_notional"] + niche_b["total_notional"]
    # Decimal rounding tolerance: within $1.
    assert abs(section1_notional - section3_notional) < 1


@pytest.mark.asyncio
async def test_combined_weekly_render_lists_same_wallet_once_per_section(
    tmp_path: Path, himalaya_binary: str
) -> None:
    """Renders a weekly payload with a wallet in both the top-markets
    and cluster-rows lists. A reader's eye check: no ghost entries,
    no duplicate market IDs inside one section."""
    scenario = Scenario(
        name="combined-render",
        himalaya_binary=himalaya_binary,
        tmp_dir=tmp_path,
    )
    payload = {
        "window_start": "2026-04-13",
        "window_end": "2026-04-20",
        "generated": "2026-04-21 08:00",
        "title": "Polymarket Insider — combined weekly",
        "metrics_rows": [
            {
                "signal": "fresh_wallet",
                "alerts_total": 3,
                "hits": 2,
                "misses": 1,
                "pending": 0,
                "precision": "66.7%",
            },
            {
                "signal": "niche_market",
                "alerts_total": 3,
                "hits": 1,
                "misses": 0,
                "pending": 2,
                "precision": "100.0%",
            },
        ],
        "top_markets_rows": [
            {"market_id": MARKET_A, "alert_count": 2},
            {"market_id": MARKET_B, "alert_count": 1},
        ],
        "cluster_rows": [
            {
                "cluster_id": "origin-abc",
                "wallet_count": 2,
                "avg_entry_delta_seconds": 60,
                "confidence": "0.70",
                "markets_in_common": 2,
            }
        ],
    }
    template_path = (
        Path(__file__).resolve().parents[2]
        / "scripts" / "templates" / "polymarket-weekly.html"
    )
    rendered = scenario.render_newsletter(
        template_path=template_path,
        report_payload=payload,
        subject="[AMI] Polymarket — Weekly (combined)",
    )
    Scenario.assert_contains_all(
        rendered, ["status=dry-run", MARKET_A, MARKET_B, "origin-abc"]
    )
    # Each market appears exactly once in top_markets_rows — the
    # YAML we fed in has unique market_ids.
    data_segment = rendered[rendered.find("\ndata=") :]
    assert data_segment.count(MARKET_A) == 1
    assert data_segment.count(MARKET_B) == 1
