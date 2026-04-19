"""Scenario 4 — Funding cluster (Théo-style).

Maps to `docs/newsletter-sections/04-funding-chains.md`. 4 fresh
wallets, all funded from the same Binance 20 hot wallet within a
48-hour window, all trading into the same 2 niche markets a few
hours later. Plus a 5th fresh wallet funded from an unrelated EOA
that must NOT appear in the cluster.

Expected:
  - 4+1 fresh_wallet alerts.
  - 4C2 = 6 pairwise shared_origin edges written to
    wallet_relationships.
  - clusters_for_origin(binance20, 7) returns exactly 4 wallets.
  - Weekly template renders the cluster summary with the cluster
    size and aggregate notional.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from polymarket_insider_tracker.backtest.replay import (
    MarketSnapshot,
    WalletSnapshot,
)
from polymarket_insider_tracker.ingestor.models import TradeEvent
from polymarket_insider_tracker.profiler.funding_graph import (
    collect_shared_origins,
    persist_clusters,
)
from polymarket_insider_tracker.storage.models import Base
from polymarket_insider_tracker.storage.repos import (
    FundingRepository,
    FundingTransferDTO,
    RelationshipRepository,
)
from tests.scenarios._harness import Scenario


BINANCE_20 = "0xf977814e90da44bfa03b6295a0616a897441acec"
UNKNOWN_ORIGIN = "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
MARKET_A = "0xmarket-axiom-reveal"
MARKET_B = "0xmarket-axiom-followup"

CLUSTER_WALLETS = (
    "0xcluster000000000000000000000000000000000a",
    "0xcluster000000000000000000000000000000000b",
    "0xcluster000000000000000000000000000000000c",
    "0xcluster000000000000000000000000000000000d",
)
OUTSIDER = "0xoutsideroutsideroutsideroutsideroutsider1"


def _trade(*, wallet: str, market: str, tx: str, size: str = "15000") -> TradeEvent:
    return TradeEvent(
        market_id=market,
        trade_id=tx,
        wallet_address=wallet,
        side="BUY",
        outcome="Yes",
        outcome_index=0,
        price=Decimal("0.2"),
        size=Decimal(size),
        timestamp=datetime(2026, 4, 19, 14, tzinfo=UTC),
        asset_id=f"0xasset-{market[-4:]}",
        market_slug="axiom-reveal",
        event_slug="axiom-reveal-event",
        event_title="Will Axiom reveal X?",
        trader_name="",
        trader_pseudonym="",
    )


@pytest.mark.asyncio
async def test_cluster_written_and_queryable(tmp_path: Path, himalaya_binary: str) -> None:
    """Task 6.2.2 + 6.2.3 — 6 edges emitted, cluster returned by origin lookup."""
    db_path = tmp_path / "cluster.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    anchor = datetime.now(UTC) - timedelta(hours=2)
    try:
        async with factory() as session:
            funding = FundingRepository(session)
            # 4 cluster wallets funded from Binance 20 within 6 hours
            for i, wallet in enumerate(CLUSTER_WALLETS):
                await funding.insert(
                    FundingTransferDTO(
                        from_address=BINANCE_20,
                        to_address=wallet,
                        amount=Decimal("1000"),
                        token="USDC",
                        tx_hash=f"0x{BINANCE_20[-4:]}{wallet[-4:]}{i}".ljust(66, "0"),
                        block_number=1,
                        timestamp=anchor + timedelta(hours=i),
                    )
                )
            # Outsider funded from a different origin — must not join.
            await funding.insert(
                FundingTransferDTO(
                    from_address=UNKNOWN_ORIGIN,
                    to_address=OUTSIDER,
                    amount=Decimal("1000"),
                    token="USDC",
                    tx_hash="0xoutsidertxtxtxtxtxtxtxtxtxtxtxtxtxtxtxtxtxtxt".ljust(66, "0"),
                    block_number=1,
                    timestamp=anchor,
                )
            )
            await session.commit()

        async with factory() as session:
            clusters = await collect_shared_origins(
                session, list(CLUSTER_WALLETS) + [OUTSIDER]
            )
            assert len(clusters) == 1  # only the Binance 20 group
            assert clusters[0].origin_address == BINANCE_20
            assert set(clusters[0].wallet_addresses) == set(CLUSTER_WALLETS)
            edges = await persist_clusters(session, clusters)
            await session.commit()
            # 4 choose 2 = 6 pairwise shared_origin edges
            assert edges == 6

        async with factory() as session:
            repo = RelationshipRepository(session)
            members = await repo.clusters_for_origin(BINANCE_20, days=7)
            assert set(members) == set(CLUSTER_WALLETS)
            # Outsider was not funded from Binance 20 → absent.
            assert OUTSIDER not in members
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_all_cluster_wallets_fire_fresh_wallet(
    tmp_path: Path, himalaya_binary: str
) -> None:
    """Task 6.2.1 — each cluster wallet, plus the outsider, fires fresh_wallet.

    The cluster wallets + outsider all have nonce=2 (fresh). The
    newsletter's cluster section is what differentiates them —
    assessment-level, they all alert.
    """
    scenario = (
        Scenario(
            name="funding-cluster-trades",
            himalaya_binary=himalaya_binary,
            tmp_dir=tmp_path,
        )
        .given_trades(
            [
                _trade(wallet=CLUSTER_WALLETS[0], market=MARKET_A, tx="0xtx-a-1"),
                _trade(wallet=CLUSTER_WALLETS[1], market=MARKET_A, tx="0xtx-a-2"),
                _trade(wallet=CLUSTER_WALLETS[2], market=MARKET_B, tx="0xtx-b-1"),
                _trade(wallet=CLUSTER_WALLETS[3], market=MARKET_B, tx="0xtx-b-2"),
                _trade(wallet=OUTSIDER, market=MARKET_A, tx="0xtx-out"),
            ]
        )
        .with_wallet_snapshots(
            {
                w: WalletSnapshot(address=w, nonce=2, first_seen_at=None, is_fresh=True)
                for w in (*CLUSTER_WALLETS, OUTSIDER)
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
    assert len(assessments) == 5
    for a in assessments:
        assert "fresh_wallet" in a.signals_triggered


@pytest.mark.asyncio
async def test_outside_window_drops_cluster(tmp_path: Path, himalaya_binary: str) -> None:
    """Task 6.4.2 — transfers 72h apart fall outside the 48h window."""
    db_path = tmp_path / "oow.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with factory() as session:
            funding = FundingRepository(session)
            anchor = datetime.now(UTC) - timedelta(days=3)
            for i, wallet in enumerate(CLUSTER_WALLETS[:2]):
                await funding.insert(
                    FundingTransferDTO(
                        from_address=BINANCE_20,
                        to_address=wallet,
                        amount=Decimal("1000"),
                        token="USDC",
                        tx_hash=f"0xoow{i}".ljust(66, "0"),
                        block_number=1,
                        timestamp=anchor + timedelta(hours=72 * i),
                    )
                )
            await session.commit()

        async with factory() as session:
            clusters = await collect_shared_origins(session, list(CLUSTER_WALLETS[:2]))
            # Only 1 wallet inside any 48h window → below min_cluster_size
            assert clusters == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_weekly_newsletter_contains_cluster_summary(
    tmp_path: Path, himalaya_binary: str, update_snapshots: bool
) -> None:
    """Task 6.2.4 + 6.3.1 — weekly template renders the cluster row + golden."""
    scenario = Scenario(
        name="funding-cluster-weekly",
        himalaya_binary=himalaya_binary,
        tmp_dir=tmp_path,
    )
    weekly_payload = {
        "window_start": "2026-04-13",
        "window_end": "2026-04-20",
        "generated": "2026-04-21 08:00",
        "title": "Polymarket Insider — weekly recap (2026-04-13 to 2026-04-20)",
        "metrics_rows": [
            {
                "signal": "fresh_wallet",
                "alerts_total": 5,
                "hits": 3,
                "misses": 1,
                "pending": 1,
                "precision": "75.0%",
            }
        ],
        "top_markets_rows": [
            {"market_id": MARKET_A, "alert_count": 3},
            {"market_id": MARKET_B, "alert_count": 2},
        ],
        "cluster_rows": [
            {
                "cluster_id": "binance-20-cluster-apr19",
                "wallet_count": 4,
                "avg_entry_delta_seconds": 120,
                "confidence": "0.80",
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
        report_payload=weekly_payload,
        subject="[AMI] Polymarket — Weekly Recap 2026-04-20",
    )
    Scenario.assert_contains_all(
        rendered,
        [
            "status=dry-run",
            "binance-20-cluster-apr19",
            "wallet_count: 4",
        ],
    )
    golden = (
        Path(__file__).parent / "fixtures" / "golden" / "funding-cluster-weekly.html"
    )
    Scenario.assert_matches_golden(rendered, golden, update=update_snapshots)
