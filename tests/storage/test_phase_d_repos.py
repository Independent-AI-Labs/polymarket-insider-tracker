"""Tests for Phase D repositories (sniper clusters + alert rollup)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from polymarket_insider_tracker.storage.models import Base
from polymarket_insider_tracker.storage.repos import (
    AlertRollupDTO,
    AlertRollupRepository,
    SniperClusterDTO,
    SniperClusterRepository,
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


@pytest.mark.asyncio
class TestSniperClusterRepository:
    async def test_insert_then_list_since_returns_cluster(self, session):
        repo = SniperClusterRepository(session)
        dto = SniperClusterDTO(
            cluster_id="clu-001",
            wallet_addresses=["0xAAA", "0xBBB", "0xCCC"],
            avg_entry_delta_seconds=42,
            confidence=Decimal("0.812"),
            markets_in_common=["0xmarket1", "0xmarket2"],
            detected_at=datetime(2026, 4, 19, 12, tzinfo=UTC),
        )
        inserted = await repo.insert_cluster(dto)
        assert inserted.id is not None
        assert inserted.wallet_addresses == ["0xaaa", "0xbbb", "0xccc"]

        rows = await repo.list_since(datetime(2026, 4, 19, tzinfo=UTC))
        assert len(rows) == 1
        assert rows[0].cluster_id == "clu-001"
        assert sorted(rows[0].wallet_addresses) == ["0xaaa", "0xbbb", "0xccc"]
        assert rows[0].markets_in_common == ["0xmarket1", "0xmarket2"]

    async def test_list_since_filters_by_cutoff(self, session):
        repo = SniperClusterRepository(session)
        old = datetime(2026, 4, 10, tzinfo=UTC)
        recent = datetime(2026, 4, 18, tzinfo=UTC)
        for i, ts in enumerate((old, recent)):
            await repo.insert_cluster(
                SniperClusterDTO(
                    cluster_id=f"clu-{i}",
                    wallet_addresses=[f"0x{i:040x}"],
                    avg_entry_delta_seconds=30,
                    confidence=Decimal("0.5"),
                    markets_in_common=["0xmkt"],
                    detected_at=ts,
                )
            )
        rows = await repo.list_since(datetime(2026, 4, 15, tzinfo=UTC))
        assert [r.cluster_id for r in rows] == ["clu-1"]

    async def test_clusters_for_wallet(self, session):
        repo = SniperClusterRepository(session)
        shared = "0xshared"
        for i in range(2):
            await repo.insert_cluster(
                SniperClusterDTO(
                    cluster_id=f"clu-{i}",
                    wallet_addresses=[shared, f"0xother{i}"],
                    avg_entry_delta_seconds=10 + i,
                    confidence=Decimal("0.6"),
                    markets_in_common=["0xmkt"],
                    detected_at=datetime(2026, 4, 19, 10 + i, tzinfo=UTC),
                )
            )
        rows = await repo.clusters_for_wallet(shared)
        assert len(rows) == 2
        for r in rows:
            assert shared.lower() in r.wallet_addresses


@pytest.mark.asyncio
class TestAlertRollupRepository:
    async def test_upsert_inserts_new(self, session):
        repo = AlertRollupRepository(session)
        day = date(2026, 4, 18)
        dto = AlertRollupDTO(
            day=day,
            market_id="0xmkt1",
            signal="fresh_wallet",
            alert_count=3,
            unique_wallets=2,
            total_notional=Decimal("15000"),
        )
        await repo.upsert(dto)
        rows = await repo.for_day(day)
        assert len(rows) == 1
        assert rows[0].alert_count == 3
        assert rows[0].total_notional == Decimal("15000")

    async def test_upsert_overwrites_existing(self, session):
        repo = AlertRollupRepository(session)
        day = date(2026, 4, 18)
        common = dict(day=day, market_id="0xmkt1", signal="fresh_wallet", unique_wallets=2)
        await repo.upsert(AlertRollupDTO(**common, alert_count=3, total_notional=Decimal("1000")))
        await repo.upsert(AlertRollupDTO(**common, alert_count=7, total_notional=Decimal("9000")))
        rows = await repo.for_day(day)
        assert len(rows) == 1, "upsert must not accumulate rows"
        assert rows[0].alert_count == 7
        assert rows[0].total_notional == Decimal("9000")

    async def test_for_day_orders_by_alert_count_desc(self, session):
        repo = AlertRollupRepository(session)
        day = date(2026, 4, 18)
        for i, count in enumerate((5, 20, 2)):
            await repo.upsert(
                AlertRollupDTO(
                    day=day,
                    market_id=f"0xmkt{i}",
                    signal="fresh_wallet",
                    alert_count=count,
                    unique_wallets=count,
                )
            )
        rows = await repo.for_day(day)
        assert [r.alert_count for r in rows] == [20, 5, 2]

    async def test_top_markets_for_window_aggregates(self, session):
        repo = AlertRollupRepository(session)
        base = date(2026, 4, 15)
        # Same market across 2 days accumulates into top ranking.
        for i, count in enumerate((5, 8)):
            await repo.upsert(
                AlertRollupDTO(
                    day=base + timedelta(days=i),
                    market_id="0xhot",
                    signal="fresh_wallet",
                    alert_count=count,
                    unique_wallets=count,
                )
            )
        await repo.upsert(
            AlertRollupDTO(
                day=base,
                market_id="0xcold",
                signal="fresh_wallet",
                alert_count=2,
                unique_wallets=1,
            )
        )
        top = await repo.top_markets_for_window(
            base, base + timedelta(days=7), limit=10
        )
        assert top[0] == ("0xhot", 13)
        assert ("0xcold", 2) in top

    async def test_for_day_returns_empty_list_when_no_rows(self, session):
        repo = AlertRollupRepository(session)
        rows = await repo.for_day(date(2026, 1, 1))
        assert rows == []
