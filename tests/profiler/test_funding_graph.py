"""Tests for the FundingGraph helper — Phase 5 of IMPLEMENTATION-TODOS."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from polymarket_insider_tracker.profiler.funding_graph import (
    DEFAULT_WINDOW_HOURS,
    collect_shared_origins,
    cluster_confidence,
    persist_clusters,
)
from polymarket_insider_tracker.storage.models import Base, FundingTransferModel
from polymarket_insider_tracker.storage.repos import (
    FundingRepository,
    FundingTransferDTO,
    RelationshipRepository,
    WalletRelationshipDTO,
)


@pytest.fixture
async def async_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(async_engine) -> AsyncSession:
    factory = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    async with factory() as s:
        yield s


async def _seed(
    session: AsyncSession,
    *,
    origin: str,
    target: str,
    amount: str,
    ts: datetime,
    tx_suffix: str = "",
) -> None:
    repo = FundingRepository(session)
    await repo.insert(
        FundingTransferDTO(
            from_address=origin,
            to_address=target,
            amount=Decimal(amount),
            token="USDC",
            tx_hash=f"0x{origin[-4:]}{target[-4:]}{tx_suffix}".ljust(66, "0"),
            block_number=1,
            timestamp=ts,
        )
    )


@pytest.mark.asyncio
class TestCollectSharedOrigins:
    async def test_returns_empty_when_no_transfers(self, session):
        result = await collect_shared_origins(session, ["0xdeadbeef"])
        assert result == []

    async def test_groups_by_origin(self, session):
        now = datetime(2026, 4, 19, 12, tzinfo=UTC)
        origin = "0xf977814e90da44bfa03b6295a0616a897441acec"
        for i, target in enumerate(("0xaaa1", "0xaaa2", "0xaaa3")):
            await _seed(
                session,
                origin=origin,
                target=target,
                amount="100",
                ts=now + timedelta(minutes=i * 10),
                tx_suffix=str(i),
            )
        clusters = await collect_shared_origins(
            session, ["0xaaa1", "0xaaa2", "0xaaa3"]
        )
        assert len(clusters) == 1
        cluster = clusters[0]
        assert cluster.origin_address == origin
        assert set(cluster.wallet_addresses) == {"0xaaa1", "0xaaa2", "0xaaa3"}

    async def test_excludes_wallets_outside_window(self, session):
        now = datetime(2026, 4, 19, 12, tzinfo=UTC)
        origin = "0xorigin000000000000000000000000000000000a"
        # Three wallets within 24h + one wallet 72h later.
        await _seed(session, origin=origin, target="0xaaa1", amount="1",
                    ts=now, tx_suffix="1")
        await _seed(session, origin=origin, target="0xaaa2", amount="1",
                    ts=now + timedelta(hours=6), tx_suffix="2")
        await _seed(session, origin=origin, target="0xaaa3", amount="1",
                    ts=now + timedelta(hours=12), tx_suffix="3")
        await _seed(session, origin=origin, target="0xaaa4", amount="1",
                    ts=now + timedelta(hours=72), tx_suffix="4")
        clusters = await collect_shared_origins(
            session, ["0xaaa1", "0xaaa2", "0xaaa3", "0xaaa4"]
        )
        assert len(clusters) == 1
        # The densest sub-cluster of 3 wallets within 48h wins;
        # 0xaaa4 is outside the window and gets dropped.
        assert set(clusters[0].wallet_addresses) == {"0xaaa1", "0xaaa2", "0xaaa3"}

    async def test_min_cluster_size_respected(self, session):
        now = datetime(2026, 4, 19, 12, tzinfo=UTC)
        origin = "0xorigin000000000000000000000000000000000b"
        await _seed(session, origin=origin, target="0xaaa1", amount="1",
                    ts=now, tx_suffix="1")
        # Only 1 funded wallet → no cluster.
        clusters = await collect_shared_origins(session, ["0xaaa1"])
        assert clusters == []

    async def test_min_cluster_size_override(self, session):
        now = datetime(2026, 4, 19, 12, tzinfo=UTC)
        origin = "0xorigin000000000000000000000000000000000c"
        for i, target in enumerate(("0xaaa1", "0xaaa2", "0xaaa3", "0xaaa4", "0xaaa5")):
            await _seed(session, origin=origin, target=target, amount="1",
                        ts=now + timedelta(minutes=i * 5), tx_suffix=str(i))
        clusters = await collect_shared_origins(
            session, ["0xaaa1", "0xaaa2", "0xaaa3", "0xaaa4", "0xaaa5"],
            min_cluster_size=4,
        )
        assert len(clusters) == 1
        assert len(clusters[0].wallet_addresses) == 5

    async def test_first_transfer_wins_on_duplicates(self, session):
        now = datetime(2026, 4, 19, 12, tzinfo=UTC)
        for i in range(3):
            await _seed(
                session,
                origin="0xoriginoriginoriginoriginoriginoriginAAAAAA",
                target="0xbbbb",
                amount="1",
                # Duplicate target with increasing timestamp — earliest
                # is kept.
                ts=now + timedelta(hours=i),
                tx_suffix=f"dup{i}",
            )
        await _seed(session, origin="0xotheroriginotheroriginotheroriginotherBBBB",
                    target="0xcccc", amount="1", ts=now, tx_suffix="c")
        clusters = await collect_shared_origins(session, ["0xbbbb", "0xcccc"])
        # bbbb appears only under its earliest origin; cccc has a
        # different origin → neither group reaches min_cluster_size 2.
        assert clusters == []


class TestClusterConfidence:
    def test_two_wallets_same_minute_is_high(self):
        now = datetime(2026, 4, 19, 12, tzinfo=UTC)
        from polymarket_insider_tracker.profiler.funding_graph import (
            SharedOriginCluster,
        )
        cluster = SharedOriginCluster(
            origin_address="0xo",
            wallet_addresses=("0xa", "0xb"),
            earliest_transfer_at=now,
            latest_transfer_at=now + timedelta(seconds=30),
            total_amount=Decimal("200"),
        )
        # hop_overlap=0 (2 wallets), simultaneity ≈ 1.0.
        assert cluster_confidence(cluster) == pytest.approx(0.55, abs=0.01)

    def test_three_wallets_late_window_lower(self):
        now = datetime(2026, 4, 19, 12, tzinfo=UTC)
        from polymarket_insider_tracker.profiler.funding_graph import (
            SharedOriginCluster,
        )
        cluster = SharedOriginCluster(
            origin_address="0xo",
            wallet_addresses=("0xa", "0xb", "0xc"),
            earliest_transfer_at=now,
            latest_transfer_at=now + timedelta(hours=47),
            total_amount=Decimal("300"),
        )
        # hop_overlap=1, simultaneity = 1 - 47/48 ≈ 0.021.
        expected = 0.5 + 0.15 + 0.05 * (1 - 47 / 48)
        assert cluster_confidence(cluster) == pytest.approx(expected, abs=0.01)

    def test_capped_at_0_95(self):
        now = datetime(2026, 4, 19, 12, tzinfo=UTC)
        from polymarket_insider_tracker.profiler.funding_graph import (
            SharedOriginCluster,
        )
        cluster = SharedOriginCluster(
            origin_address="0xo",
            wallet_addresses=tuple(f"0x{i:040x}" for i in range(20)),
            earliest_transfer_at=now,
            latest_transfer_at=now + timedelta(seconds=1),
            total_amount=Decimal("0"),
        )
        # hop_overlap cap = 2, simultaneity ≈ 1 → 0.5 + 0.30 + 0.05 = 0.85
        assert cluster_confidence(cluster) <= 0.95


@pytest.mark.asyncio
class TestPersistClusters:
    async def test_writes_pairwise_edges(self, session):
        now = datetime(2026, 4, 19, 12, tzinfo=UTC)
        from polymarket_insider_tracker.profiler.funding_graph import (
            SharedOriginCluster,
        )
        cluster = SharedOriginCluster(
            origin_address="0xo",
            wallet_addresses=("0xa", "0xb", "0xc"),  # 3 → 3 edges
            earliest_transfer_at=now,
            latest_transfer_at=now,
            total_amount=Decimal("0"),
        )
        written = await persist_clusters(session, [cluster])
        assert written == 3
        repo = RelationshipRepository(session)
        assert sorted(await repo.get_related_wallets("0xa")) == ["0xb", "0xc"]

    async def test_idempotent_reinsert(self, session):
        now = datetime(2026, 4, 19, 12, tzinfo=UTC)
        from polymarket_insider_tracker.profiler.funding_graph import (
            SharedOriginCluster,
        )
        cluster = SharedOriginCluster(
            origin_address="0xo",
            wallet_addresses=("0xa", "0xb"),
            earliest_transfer_at=now,
            latest_transfer_at=now,
            total_amount=Decimal("0"),
        )
        await persist_clusters(session, [cluster])
        # Re-run with same input — upsert must not duplicate rows.
        await persist_clusters(session, [cluster])
        repo = RelationshipRepository(session)
        edges = await repo.get_relationships("0xa", "shared_origin")
        assert len(edges) == 1


@pytest.mark.asyncio
class TestClustersForOrigin:
    async def test_returns_funded_wallets_in_window(self, session):
        now = datetime.now(UTC)
        origin = "0xbinance20000000000000000000000000000000ce"
        # Seed 3 funding transfers from the origin within the last week.
        for i, target in enumerate(("0xaaa1", "0xaaa2", "0xaaa3")):
            await _seed(
                session, origin=origin, target=target, amount="100",
                ts=now - timedelta(days=1, hours=i), tx_suffix=str(i),
            )
        # Persist the cluster edges.
        clusters = await collect_shared_origins(
            session, ["0xaaa1", "0xaaa2", "0xaaa3"]
        )
        await persist_clusters(session, clusters)
        # Query back.
        repo = RelationshipRepository(session)
        members = await repo.clusters_for_origin(origin, days=7)
        assert set(members) == {"0xaaa1", "0xaaa2", "0xaaa3"}

    async def test_outside_window_excluded(self, session):
        now = datetime.now(UTC)
        origin = "0xoriginxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        await _seed(session, origin=origin, target="0xold1", amount="1",
                    ts=now - timedelta(days=60), tx_suffix="x")
        await _seed(session, origin=origin, target="0xold2", amount="1",
                    ts=now - timedelta(days=60, hours=2), tx_suffix="y")
        clusters = await collect_shared_origins(session, ["0xold1", "0xold2"])
        await persist_clusters(session, clusters)
        repo = RelationshipRepository(session)
        members = await repo.clusters_for_origin(origin, days=30)
        assert members == []

    async def test_single_funded_wallet_returns_singleton(self, session):
        """A single funded wallet is not a cluster — helper returns the
        address anyway so operators can inspect it; persist_clusters
        writes zero edges because min_cluster_size guards the writer."""
        now = datetime.now(UTC)
        origin = "0xonewalletoriginnnnnnnnnnnnnnnnnnnnnnnnnnnn"
        await _seed(session, origin=origin, target="0xonly", amount="1",
                    ts=now - timedelta(hours=1), tx_suffix="only")
        repo = RelationshipRepository(session)
        members = await repo.clusters_for_origin(origin, days=7)
        assert members == ["0xonly"]
