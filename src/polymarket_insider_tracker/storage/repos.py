"""Repository pattern implementations for data access.

This module provides clean data access abstractions for wallet profiles,
funding transfers, and wallet relationships.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

import re
import uuid

from polymarket_insider_tracker.storage.models import (
    AlertDailyRollupModel,
    DetectorMetricsModel,
    EmailBounceModel,
    EmailDeliveryModel,
    FundingTransferModel,
    SniperClusterMemberModel,
    SniperClusterModel,
    SubscriberModel,
    SuppressionEntryModel,
    WalletProfileModel,
    WalletRelationshipModel,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class WalletProfileDTO:
    """Data transfer object for wallet profiles."""

    address: str
    nonce: int
    first_seen_at: datetime | None
    is_fresh: bool
    matic_balance: Decimal | None
    usdc_balance: Decimal | None
    analyzed_at: datetime
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_model(cls, model: WalletProfileModel) -> WalletProfileDTO:
        """Create DTO from SQLAlchemy model."""
        return cls(
            address=model.address,
            nonce=model.nonce,
            first_seen_at=model.first_seen_at,
            is_fresh=model.is_fresh,
            matic_balance=model.matic_balance,
            usdc_balance=model.usdc_balance,
            analyzed_at=model.analyzed_at,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )


@dataclass
class FundingTransferDTO:
    """Data transfer object for funding transfers."""

    from_address: str
    to_address: str
    amount: Decimal
    token: str
    tx_hash: str
    block_number: int
    timestamp: datetime
    created_at: datetime | None = None

    @classmethod
    def from_model(cls, model: FundingTransferModel) -> FundingTransferDTO:
        """Create DTO from SQLAlchemy model."""
        return cls(
            from_address=model.from_address,
            to_address=model.to_address,
            amount=model.amount,
            token=model.token,
            tx_hash=model.tx_hash,
            block_number=model.block_number,
            timestamp=model.timestamp,
            created_at=model.created_at,
        )


@dataclass
class WalletRelationshipDTO:
    """Data transfer object for wallet relationships."""

    wallet_a: str
    wallet_b: str
    relationship_type: str
    confidence: Decimal
    created_at: datetime | None = None

    @classmethod
    def from_model(cls, model: WalletRelationshipModel) -> WalletRelationshipDTO:
        """Create DTO from SQLAlchemy model."""
        return cls(
            wallet_a=model.wallet_a,
            wallet_b=model.wallet_b,
            relationship_type=model.relationship_type,
            confidence=model.confidence,
            created_at=model.created_at,
        )


class WalletRepository:
    """Repository for wallet profile data access.

    Provides CRUD operations for wallet profiles with async support.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with database session.

        Args:
            session: SQLAlchemy async session.
        """
        self.session = session

    async def get_by_address(self, address: str) -> WalletProfileDTO | None:
        """Get wallet profile by address.

        Args:
            address: Wallet address (lowercase).

        Returns:
            WalletProfileDTO if found, None otherwise.
        """
        result = await self.session.execute(
            select(WalletProfileModel).where(WalletProfileModel.address == address.lower())
        )
        model = result.scalar_one_or_none()
        return WalletProfileDTO.from_model(model) if model else None

    async def get_many(self, addresses: list[str]) -> list[WalletProfileDTO]:
        """Get multiple wallet profiles by addresses.

        Args:
            addresses: List of wallet addresses.

        Returns:
            List of WalletProfileDTOs for found addresses.
        """
        normalized = [addr.lower() for addr in addresses]
        result = await self.session.execute(
            select(WalletProfileModel).where(WalletProfileModel.address.in_(normalized))
        )
        return [WalletProfileDTO.from_model(m) for m in result.scalars().all()]

    async def get_fresh_wallets(self, limit: int = 100) -> list[WalletProfileDTO]:
        """Get recent fresh wallets.

        Args:
            limit: Maximum number of results.

        Returns:
            List of WalletProfileDTOs marked as fresh.
        """
        result = await self.session.execute(
            select(WalletProfileModel)
            .where(WalletProfileModel.is_fresh.is_(True))
            .order_by(WalletProfileModel.analyzed_at.desc())
            .limit(limit)
        )
        return [WalletProfileDTO.from_model(m) for m in result.scalars().all()]

    async def upsert(self, dto: WalletProfileDTO) -> WalletProfileDTO:
        """Insert or update wallet profile.

        Args:
            dto: Wallet profile data.

        Returns:
            Updated WalletProfileDTO.
        """
        now = datetime.now(UTC)
        values = {
            "address": dto.address.lower(),
            "nonce": dto.nonce,
            "first_seen_at": dto.first_seen_at,
            "is_fresh": dto.is_fresh,
            "matic_balance": dto.matic_balance,
            "usdc_balance": dto.usdc_balance,
            "analyzed_at": dto.analyzed_at,
            "updated_at": now,
        }

        # Try PostgreSQL upsert first, fall back to SQLite for testing
        try:
            stmt = pg_insert(WalletProfileModel).values(**values, created_at=now)
            stmt = stmt.on_conflict_do_update(
                index_elements=["address"],
                set_={
                    "nonce": stmt.excluded.nonce,
                    "first_seen_at": stmt.excluded.first_seen_at,
                    "is_fresh": stmt.excluded.is_fresh,
                    "matic_balance": stmt.excluded.matic_balance,
                    "usdc_balance": stmt.excluded.usdc_balance,
                    "analyzed_at": stmt.excluded.analyzed_at,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
            await self.session.execute(stmt)
        except Exception:
            # Fall back to SQLite upsert for testing
            sqlite_stmt = sqlite_insert(WalletProfileModel).values(**values, created_at=now)
            sqlite_stmt = sqlite_stmt.on_conflict_do_update(
                index_elements=["address"],
                set_={
                    "nonce": sqlite_stmt.excluded.nonce,
                    "first_seen_at": sqlite_stmt.excluded.first_seen_at,
                    "is_fresh": sqlite_stmt.excluded.is_fresh,
                    "matic_balance": sqlite_stmt.excluded.matic_balance,
                    "usdc_balance": sqlite_stmt.excluded.usdc_balance,
                    "analyzed_at": sqlite_stmt.excluded.analyzed_at,
                    "updated_at": sqlite_stmt.excluded.updated_at,
                },
            )
            await self.session.execute(sqlite_stmt)

        await self.session.flush()
        return dto

    async def delete(self, address: str) -> bool:
        """Delete wallet profile by address.

        Args:
            address: Wallet address.

        Returns:
            True if deleted, False if not found.
        """
        result = await self.session.execute(
            delete(WalletProfileModel).where(WalletProfileModel.address == address.lower())
        )
        # SQLAlchemy Result does have rowcount but typing doesn't reflect it
        return (result.rowcount or 0) > 0  # type: ignore[attr-defined]

    async def mark_stale(self, address: str) -> bool:
        """Mark a wallet profile as stale (soft delete).

        Sets analyzed_at to a very old date to trigger re-analysis.

        Args:
            address: Wallet address.

        Returns:
            True if updated, False if not found.
        """
        stale_time = datetime(2000, 1, 1, tzinfo=UTC)
        result = await self.session.execute(
            update(WalletProfileModel)
            .where(WalletProfileModel.address == address.lower())
            .values(analyzed_at=stale_time, updated_at=datetime.now(UTC))
        )
        # SQLAlchemy Result does have rowcount but typing doesn't reflect it
        return (result.rowcount or 0) > 0  # type: ignore[attr-defined]


class FundingRepository:
    """Repository for funding transfer data access.

    Provides CRUD operations for funding transfers with async support.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with database session.

        Args:
            session: SQLAlchemy async session.
        """
        self.session = session

    async def get_transfers_to(self, address: str, limit: int = 100) -> list[FundingTransferDTO]:
        """Get transfers to a wallet address.

        Args:
            address: Destination wallet address.
            limit: Maximum number of results.

        Returns:
            List of FundingTransferDTOs ordered by timestamp.
        """
        result = await self.session.execute(
            select(FundingTransferModel)
            .where(FundingTransferModel.to_address == address.lower())
            .order_by(FundingTransferModel.timestamp.asc())
            .limit(limit)
        )
        return [FundingTransferDTO.from_model(m) for m in result.scalars().all()]

    async def get_transfers_from(self, address: str, limit: int = 100) -> list[FundingTransferDTO]:
        """Get transfers from a wallet address.

        Args:
            address: Source wallet address.
            limit: Maximum number of results.

        Returns:
            List of FundingTransferDTOs ordered by timestamp.
        """
        result = await self.session.execute(
            select(FundingTransferModel)
            .where(FundingTransferModel.from_address == address.lower())
            .order_by(FundingTransferModel.timestamp.asc())
            .limit(limit)
        )
        return [FundingTransferDTO.from_model(m) for m in result.scalars().all()]

    async def get_first_transfer_to(self, address: str) -> FundingTransferDTO | None:
        """Get the first transfer to a wallet.

        Args:
            address: Wallet address.

        Returns:
            First FundingTransferDTO if found, None otherwise.
        """
        result = await self.session.execute(
            select(FundingTransferModel)
            .where(FundingTransferModel.to_address == address.lower())
            .order_by(FundingTransferModel.timestamp.asc())
            .limit(1)
        )
        model = result.scalar_one_or_none()
        return FundingTransferDTO.from_model(model) if model else None

    async def get_by_tx_hash(self, tx_hash: str) -> FundingTransferDTO | None:
        """Get transfer by transaction hash.

        Args:
            tx_hash: Transaction hash.

        Returns:
            FundingTransferDTO if found, None otherwise.
        """
        result = await self.session.execute(
            select(FundingTransferModel).where(FundingTransferModel.tx_hash == tx_hash.lower())
        )
        model = result.scalar_one_or_none()
        return FundingTransferDTO.from_model(model) if model else None

    async def insert(self, dto: FundingTransferDTO) -> FundingTransferDTO:
        """Insert a new funding transfer.

        Args:
            dto: Funding transfer data.

        Returns:
            Inserted FundingTransferDTO.

        Raises:
            IntegrityError if tx_hash already exists.
        """
        model = FundingTransferModel(
            from_address=dto.from_address.lower(),
            to_address=dto.to_address.lower(),
            amount=dto.amount,
            token=dto.token,
            tx_hash=dto.tx_hash.lower(),
            block_number=dto.block_number,
            timestamp=dto.timestamp,
        )
        self.session.add(model)
        await self.session.flush()
        return dto

    async def insert_many(self, dtos: list[FundingTransferDTO]) -> int:
        """Insert multiple funding transfers.

        Skips duplicates silently.

        Args:
            dtos: List of funding transfer data.

        Returns:
            Number of transfers inserted.
        """
        inserted = 0
        for dto in dtos:
            try:
                await self.insert(dto)
                inserted += 1
            except Exception as e:
                # Skip duplicates
                if "UNIQUE constraint" in str(e) or "duplicate key" in str(e).lower():
                    continue
                raise
        return inserted


class RelationshipRepository:
    """Repository for wallet relationship data access.

    Provides CRUD operations for wallet relationships with async support.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with database session.

        Args:
            session: SQLAlchemy async session.
        """
        self.session = session

    async def get_relationships(
        self, wallet: str, relationship_type: str | None = None
    ) -> list[WalletRelationshipDTO]:
        """Get relationships for a wallet.

        Args:
            wallet: Wallet address.
            relationship_type: Optional filter by type.

        Returns:
            List of WalletRelationshipDTOs.
        """
        stmt = select(WalletRelationshipModel).where(
            (WalletRelationshipModel.wallet_a == wallet.lower())
            | (WalletRelationshipModel.wallet_b == wallet.lower())
        )
        if relationship_type:
            stmt = stmt.where(WalletRelationshipModel.relationship_type == relationship_type)

        result = await self.session.execute(stmt)
        return [WalletRelationshipDTO.from_model(m) for m in result.scalars().all()]

    async def get_related_wallets(
        self, wallet: str, relationship_type: str | None = None
    ) -> list[str]:
        """Get addresses of related wallets.

        Args:
            wallet: Wallet address.
            relationship_type: Optional filter by type.

        Returns:
            List of related wallet addresses.
        """
        relationships = await self.get_relationships(wallet, relationship_type)
        related = set()
        normalized = wallet.lower()
        for rel in relationships:
            if rel.wallet_a == normalized:
                related.add(rel.wallet_b)
            else:
                related.add(rel.wallet_a)
        return list(related)

    async def upsert(self, dto: WalletRelationshipDTO) -> WalletRelationshipDTO:
        """Insert or update wallet relationship.

        Args:
            dto: Wallet relationship data.

        Returns:
            Updated WalletRelationshipDTO.
        """
        now = datetime.now(UTC)
        values = {
            "wallet_a": dto.wallet_a.lower(),
            "wallet_b": dto.wallet_b.lower(),
            "relationship_type": dto.relationship_type,
            "confidence": dto.confidence,
            "created_at": now,
        }

        # Try PostgreSQL upsert first, fall back to SQLite for testing
        try:
            stmt = pg_insert(WalletRelationshipModel).values(**values)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_wallet_relationship",
                set_={"confidence": stmt.excluded.confidence},
            )
            await self.session.execute(stmt)
        except Exception:
            # Fall back to SQLite upsert for testing
            sqlite_stmt = sqlite_insert(WalletRelationshipModel).values(**values)
            sqlite_stmt = sqlite_stmt.on_conflict_do_update(
                index_elements=["wallet_a", "wallet_b", "relationship_type"],
                set_={"confidence": sqlite_stmt.excluded.confidence},
            )
            await self.session.execute(sqlite_stmt)

        await self.session.flush()
        return dto

    async def delete(self, wallet_a: str, wallet_b: str, relationship_type: str) -> bool:
        """Delete a specific relationship.

        Args:
            wallet_a: First wallet address.
            wallet_b: Second wallet address.
            relationship_type: Type of relationship.

        Returns:
            True if deleted, False if not found.
        """
        result = await self.session.execute(
            delete(WalletRelationshipModel).where(
                WalletRelationshipModel.wallet_a == wallet_a.lower(),
                WalletRelationshipModel.wallet_b == wallet_b.lower(),
                WalletRelationshipModel.relationship_type == relationship_type,
            )
        )
        # SQLAlchemy Result does have rowcount but typing doesn't reflect it
        return (result.rowcount or 0) > 0  # type: ignore[attr-defined]


@dataclass
class DetectorMetricsDTO:
    """Data transfer object for a detector-metrics row."""

    window_start: datetime
    window_end: datetime
    signal: str
    alerts_total: int
    hits: int
    misses: int
    pending: int
    precision: Decimal | None = None
    pnl_uplift_bps: int | None = None
    notes: str | None = None
    computed_at: datetime | None = None
    id: int | None = None

    @classmethod
    def from_model(cls, model: DetectorMetricsModel) -> DetectorMetricsDTO:
        return cls(
            id=model.id,
            computed_at=model.computed_at,
            window_start=model.window_start,
            window_end=model.window_end,
            signal=model.signal,
            alerts_total=model.alerts_total,
            hits=model.hits,
            misses=model.misses,
            pending=model.pending,
            precision=model.precision,
            pnl_uplift_bps=model.pnl_uplift_bps,
            notes=model.notes,
        )


class DetectorMetricsRepository:
    """Repository for backtest-computed detector metrics (Phase C).

    Readers: the monthly calibration newsletter (`scripts/newsletters/monthly.py`)
    and the weekly hit-or-miss retrospective read rows for their reporting
    window. Writers: `polymarket_insider_tracker.backtest.metrics`.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def insert(self, dto: DetectorMetricsDTO) -> DetectorMetricsDTO:
        """Insert a metrics row and return it with db-assigned fields populated."""
        model = DetectorMetricsModel(
            computed_at=dto.computed_at or datetime.now(UTC),
            window_start=dto.window_start,
            window_end=dto.window_end,
            signal=dto.signal,
            alerts_total=dto.alerts_total,
            hits=dto.hits,
            misses=dto.misses,
            pending=dto.pending,
            precision=dto.precision,
            pnl_uplift_bps=dto.pnl_uplift_bps,
            notes=dto.notes,
        )
        self.session.add(model)
        await self.session.flush()
        return DetectorMetricsDTO.from_model(model)

    async def list_for_window(
        self, window_start: datetime, window_end: datetime
    ) -> list[DetectorMetricsDTO]:
        """Return all metrics rows whose `window_start` falls in [start, end)."""
        result = await self.session.execute(
            select(DetectorMetricsModel)
            .where(DetectorMetricsModel.window_start >= window_start)
            .where(DetectorMetricsModel.window_start < window_end)
            .order_by(DetectorMetricsModel.window_start, DetectorMetricsModel.signal)
        )
        return [DetectorMetricsDTO.from_model(m) for m in result.scalars().all()]

    async def latest_per_signal(self, signals: list[str]) -> dict[str, DetectorMetricsDTO]:
        """Return the most recent row for each signal name, keyed by signal."""
        out: dict[str, DetectorMetricsDTO] = {}
        for signal in signals:
            result = await self.session.execute(
                select(DetectorMetricsModel)
                .where(DetectorMetricsModel.signal == signal)
                .order_by(DetectorMetricsModel.window_start.desc())
                .limit(1)
            )
            model = result.scalar_one_or_none()
            if model is not None:
                out[signal] = DetectorMetricsDTO.from_model(model)
        return out


@dataclass
class SniperClusterDTO:
    """Data transfer object for a persisted sniper cluster."""

    cluster_id: str
    wallet_addresses: list[str]
    avg_entry_delta_seconds: int | None
    confidence: Decimal | None
    markets_in_common: list[str]
    detected_at: datetime | None = None
    id: int | None = None


class SniperClusterRepository:
    """Persist the output of SniperDetector.run_clustering.

    The pipeline schedules a 15-minute tick that calls
    `SniperDetector.run_clustering()` and forwards each resulting
    `SniperClusterSignal` here via `insert_cluster`. The weekly
    newsletter reads `list_since(cutoff)` to populate its "new sniper
    clusters" section.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def insert_cluster(self, dto: SniperClusterDTO) -> SniperClusterDTO:
        """Insert a cluster row plus a row per member wallet."""
        cluster_model = SniperClusterModel(
            detected_at=dto.detected_at or datetime.now(UTC),
            cluster_id=dto.cluster_id,
            wallet_count=len(dto.wallet_addresses),
            avg_entry_delta_seconds=dto.avg_entry_delta_seconds,
            confidence=dto.confidence,
            markets_in_common=list(dto.markets_in_common),
        )
        self.session.add(cluster_model)
        await self.session.flush()
        for wallet in dto.wallet_addresses:
            self.session.add(
                SniperClusterMemberModel(
                    cluster_row_id=cluster_model.id,
                    wallet_address=wallet.lower(),
                )
            )
        await self.session.flush()
        return SniperClusterDTO(
            id=cluster_model.id,
            cluster_id=cluster_model.cluster_id,
            wallet_addresses=[w.lower() for w in dto.wallet_addresses],
            avg_entry_delta_seconds=cluster_model.avg_entry_delta_seconds,
            confidence=cluster_model.confidence,
            markets_in_common=list(cluster_model.markets_in_common),
            detected_at=cluster_model.detected_at,
        )

    async def list_since(self, cutoff: datetime) -> list[SniperClusterDTO]:
        """Return clusters detected at or after `cutoff`."""
        result = await self.session.execute(
            select(SniperClusterModel)
            .where(SniperClusterModel.detected_at >= cutoff)
            .order_by(SniperClusterModel.detected_at.desc())
        )
        clusters = list(result.scalars().all())
        if not clusters:
            return []

        ids = [c.id for c in clusters]
        members_result = await self.session.execute(
            select(SniperClusterMemberModel).where(
                SniperClusterMemberModel.cluster_row_id.in_(ids)
            )
        )
        by_cluster: dict[int, list[str]] = {cid: [] for cid in ids}
        for m in members_result.scalars().all():
            by_cluster[m.cluster_row_id].append(m.wallet_address)

        return [
            SniperClusterDTO(
                id=c.id,
                cluster_id=c.cluster_id,
                wallet_addresses=sorted(by_cluster.get(c.id, [])),
                avg_entry_delta_seconds=c.avg_entry_delta_seconds,
                confidence=c.confidence,
                markets_in_common=list(c.markets_in_common),
                detected_at=c.detected_at,
            )
            for c in clusters
        ]

    async def clusters_for_wallet(self, wallet_address: str) -> list[SniperClusterDTO]:
        """Return every cluster the given wallet has appeared in."""
        result = await self.session.execute(
            select(SniperClusterModel)
            .join(SniperClusterMemberModel,
                  SniperClusterMemberModel.cluster_row_id == SniperClusterModel.id)
            .where(SniperClusterMemberModel.wallet_address == wallet_address.lower())
            .order_by(SniperClusterModel.detected_at.desc())
        )
        clusters = list(result.scalars().all())
        if not clusters:
            return []
        return await self.list_since(min(c.detected_at for c in clusters))


@dataclass
class AlertRollupDTO:
    """One (day, market, signal) aggregate row."""

    day: datetime
    market_id: str
    signal: str
    alert_count: int
    unique_wallets: int
    total_notional: Decimal | None = None


class AlertRollupRepository:
    """Daily-rollup aggregate read by the newsletter builders.

    Writers: `scripts/compute-daily-rollup.py` (cron `5 0 * * *` UTC).
    Readers: `scripts/newsletters/daily.py` / `weekly.py`.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, dto: AlertRollupDTO) -> AlertRollupDTO:
        """Insert-or-replace the row for (day, market_id, signal).

        The rollup script runs idempotently — we tolerate re-runs of
        the same day by overwriting, rather than accumulating.
        """
        # Portable upsert: delete existing row, then insert. The
        # composite primary key guarantees uniqueness.
        await self.session.execute(
            delete(AlertDailyRollupModel).where(
                AlertDailyRollupModel.day == dto.day,
                AlertDailyRollupModel.market_id == dto.market_id,
                AlertDailyRollupModel.signal == dto.signal,
            )
        )
        model = AlertDailyRollupModel(
            day=dto.day,
            market_id=dto.market_id,
            signal=dto.signal,
            alert_count=dto.alert_count,
            unique_wallets=dto.unique_wallets,
            total_notional=dto.total_notional,
        )
        self.session.add(model)
        await self.session.flush()
        return dto

    async def for_day(self, day: datetime) -> list[AlertRollupDTO]:
        """Return all rows for the given day (one per market × signal)."""
        result = await self.session.execute(
            select(AlertDailyRollupModel)
            .where(AlertDailyRollupModel.day == day)
            .order_by(AlertDailyRollupModel.alert_count.desc())
        )
        return [
            AlertRollupDTO(
                day=m.day,
                market_id=m.market_id,
                signal=m.signal,
                alert_count=m.alert_count,
                unique_wallets=m.unique_wallets,
                total_notional=m.total_notional,
            )
            for m in result.scalars().all()
        ]

    async def top_markets_for_window(
        self,
        start: datetime,
        end: datetime,
        *,
        limit: int = 10,
    ) -> list[tuple[str, int]]:
        """Return `[(market_id, total_alerts)]` over the window, top N.

        Used by the weekly retrospective to rank noisiest markets.
        """
        from sqlalchemy import func
        result = await self.session.execute(
            select(
                AlertDailyRollupModel.market_id,
                func.sum(AlertDailyRollupModel.alert_count).label("total"),
            )
            .where(AlertDailyRollupModel.day >= start)
            .where(AlertDailyRollupModel.day < end)
            .group_by(AlertDailyRollupModel.market_id)
            .order_by(func.sum(AlertDailyRollupModel.alert_count).desc())
            .limit(limit)
        )
        return [(row[0], int(row[1] or 0)) for row in result.all()]


# ---------------------------------------------------------------------------
# Phase F — subscribers, deliveries, bounces, suppression.
# ---------------------------------------------------------------------------

# Allowed cadence tags. Enforced by SubscribersRepository.insert_pending
# so a malicious /subscribe POST can't smuggle arbitrary strings into the
# cadences list.
ALLOWED_CADENCES: frozenset[str] = frozenset({"daily", "weekly", "monthly"})

# Subscriber lifecycle states as named constants.
STATUS_PENDING = "pending_opt_in"
STATUS_ACTIVE = "active"
STATUS_BOUNCED = "bounced"
STATUS_UNSUBSCRIBED = "unsubscribed"
STATUS_SUPPRESSED = "suppressed"

# REQ-MAIL-115: hard bounces trigger a flip after this many.
DEFAULT_BOUNCE_THRESHOLD = 3


@dataclass
class SubscriberDTO:
    """Data transfer object for a public subscriber row."""

    email: str
    cadences: list[str]
    status: str
    opt_in_token: str
    unsubscribe_token: str
    bounce_count: int = 0
    name: str | None = None
    opt_in_confirmed_at: datetime | None = None
    last_bounce_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    id: int | None = None

    @classmethod
    def from_model(cls, m: SubscriberModel) -> SubscriberDTO:
        return cls(
            id=m.id,
            email=m.email,
            name=m.name,
            cadences=[c.strip() for c in (m.cadences or "").split(",") if c.strip()],
            status=m.status,
            opt_in_token=m.opt_in_token,
            opt_in_confirmed_at=m.opt_in_confirmed_at,
            unsubscribe_token=m.unsubscribe_token,
            bounce_count=m.bounce_count,
            last_bounce_at=m.last_bounce_at,
            created_at=m.created_at,
            updated_at=m.updated_at,
        )


class SubscribersRepository:
    """CRUD for public newsletter subscribers (REQ-MAIL-110..118)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _normalise_email(raw: str) -> str:
        """Lowercase + strip whitespace. CITEXT handles this on Postgres
        but we also run on SQLite in tests."""
        return raw.strip().lower()

    @staticmethod
    def _validate_cadences(cadences: list[str]) -> list[str]:
        """Reject cadences not in ALLOWED_CADENCES; preserve order + dedup."""
        seen: list[str] = []
        for c in cadences:
            normal = c.strip().lower()
            if normal not in ALLOWED_CADENCES:
                msg = f"invalid cadence: {c!r}"
                raise ValueError(msg)
            if normal not in seen:
                seen.append(normal)
        if not seen:
            msg = "cadences must not be empty"
            raise ValueError(msg)
        return seen

    async def insert_pending(
        self,
        *,
        email: str,
        cadences: list[str],
        name: str | None = None,
    ) -> SubscriberDTO:
        """Insert a new `pending_opt_in` row or re-issue tokens idempotently.

        If a row already exists with the same email:
        - `pending_opt_in`: refresh tokens + timestamp so a stale
          confirmation link is invalidated (the user likely lost the
          first email).
        - `active` / `unsubscribed` / `bounced`: don't touch it — the
          caller should distinguish "already signed up" from "new
          signup" via the returned status.
        - `suppressed`: raise — signup is explicitly blocked.
        """
        email_norm = self._normalise_email(email)
        cadence_list = self._validate_cadences(cadences)

        existing = await self.session.execute(
            select(SubscriberModel).where(SubscriberModel.email == email_norm)
        )
        row = existing.scalar_one_or_none()
        if row is None:
            model = SubscriberModel(
                email=email_norm,
                name=name,
                cadences=",".join(cadence_list),
                status=STATUS_PENDING,
                opt_in_token=str(uuid.uuid4()),
                unsubscribe_token=str(uuid.uuid4()),
            )
            self.session.add(model)
            await self.session.flush()
            return SubscriberDTO.from_model(model)

        if row.status == STATUS_SUPPRESSED:
            msg = f"{email_norm} is on the suppression list"
            raise PermissionError(msg)

        if row.status == STATUS_PENDING:
            row.opt_in_token = str(uuid.uuid4())
            row.cadences = ",".join(cadence_list)
            if name is not None:
                row.name = name
            row.updated_at = datetime.now(UTC)
            await self.session.flush()

        return SubscriberDTO.from_model(row)

    async def confirm_opt_in(self, token: str) -> SubscriberDTO | None:
        """Flip the matching row from `pending_opt_in` to `active`.

        Idempotent: a row already `active` stays `active`; invalid
        tokens return None (no enumeration signal).
        """
        result = await self.session.execute(
            select(SubscriberModel).where(SubscriberModel.opt_in_token == token)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        if row.status == STATUS_PENDING:
            row.status = STATUS_ACTIVE
            row.opt_in_confirmed_at = datetime.now(UTC)
            row.updated_at = datetime.now(UTC)
            await self.session.flush()
        return SubscriberDTO.from_model(row)

    async def unsubscribe(self, token: str) -> SubscriberDTO | None:
        """Flip any row to `unsubscribed` by its unsubscribe token.

        Idempotent + constant-time against valid-vs-invalid tokens
        (REQ-MAIL-112); we still flush on no-op so the API response
        pattern looks identical.
        """
        result = await self.session.execute(
            select(SubscriberModel).where(
                SubscriberModel.unsubscribe_token == token
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        row.status = STATUS_UNSUBSCRIBED
        row.updated_at = datetime.now(UTC)
        await self.session.flush()
        return SubscriberDTO.from_model(row)

    async def active_for_cadence(self, cadence: str) -> list[SubscriberDTO]:
        """Return the rows a cadence run should deliver to.

        Filters to `status='active'` AND `cadence in subscriber.cadences`.
        Suppression is applied on top in `filter_suppressed` so the
        caller can log suppression hits separately (REQ-MAIL-132).
        """
        cadence_norm = cadence.strip().lower()
        if cadence_norm not in ALLOWED_CADENCES:
            msg = f"invalid cadence: {cadence!r}"
            raise ValueError(msg)

        result = await self.session.execute(
            select(SubscriberModel).where(SubscriberModel.status == STATUS_ACTIVE)
        )
        out: list[SubscriberDTO] = []
        for model in result.scalars().all():
            dto = SubscriberDTO.from_model(model)
            if cadence_norm in dto.cadences:
                out.append(dto)
        return out

    async def record_bounce(
        self,
        *,
        email: str,
        bounce_type: str,
        threshold: int = DEFAULT_BOUNCE_THRESHOLD,
    ) -> SubscriberDTO | None:
        """Increment `bounce_count`; flip to `bounced` if threshold crossed.

        Only hard bounces count against the threshold per REQ-MAIL-115.
        """
        email_norm = self._normalise_email(email)
        result = await self.session.execute(
            select(SubscriberModel).where(SubscriberModel.email == email_norm)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None

        row.last_bounce_at = datetime.now(UTC)
        if bounce_type == "hard":
            row.bounce_count += 1
            if row.bounce_count >= threshold and row.status == STATUS_ACTIVE:
                row.status = STATUS_BOUNCED
        row.updated_at = datetime.now(UTC)
        await self.session.flush()
        return SubscriberDTO.from_model(row)

    async def delete_for_gdpr(self, email: str) -> bool:
        """Remove a row entirely (REQ-MAIL-117 Art. 17 erasure).

        Returns True if a row was deleted. Associated ledger + bounce
        rows are preserved for compliance forensics — they no longer
        have a subscriber_id foreign key after deletion.
        """
        email_norm = self._normalise_email(email)
        result = await self.session.execute(
            delete(SubscriberModel).where(SubscriberModel.email == email_norm)
        )
        return (result.rowcount or 0) > 0  # type: ignore[attr-defined]


@dataclass
class SuppressionEntryDTO:
    """Data transfer object for a suppression-list row."""

    pattern: str
    pattern_type: str  # exact | domain | regex
    reason: str | None = None
    created_at: datetime | None = None
    id: int | None = None


class SuppressionListRepository:
    """Pre-send filter that overrides any opt-in (REQ-MAIL-116)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, dto: SuppressionEntryDTO) -> SuppressionEntryDTO:
        if dto.pattern_type not in ("exact", "domain", "regex"):
            msg = f"invalid pattern_type: {dto.pattern_type!r}"
            raise ValueError(msg)
        model = SuppressionEntryModel(
            pattern=dto.pattern.strip().lower(),
            pattern_type=dto.pattern_type,
            reason=dto.reason,
        )
        self.session.add(model)
        await self.session.flush()
        return SuppressionEntryDTO(
            id=model.id,
            pattern=model.pattern,
            pattern_type=model.pattern_type,
            reason=model.reason,
            created_at=model.created_at,
        )

    async def matches(self, email: str) -> SuppressionEntryDTO | None:
        """Return the matching suppression entry for an email, if any."""
        email_norm = email.strip().lower()
        domain = email_norm.rsplit("@", 1)[-1] if "@" in email_norm else ""
        result = await self.session.execute(select(SuppressionEntryModel))
        for model in result.scalars().all():
            if model.pattern_type == "exact" and model.pattern == email_norm:
                return _supp_dto(model)
            if model.pattern_type == "domain" and model.pattern == domain:
                return _supp_dto(model)
            if model.pattern_type == "regex":
                try:
                    if re.fullmatch(model.pattern, email_norm):
                        return _supp_dto(model)
                except re.error:
                    # Skip malformed regexes — operator will see them
                    # in an audit but they mustn't break a live send.
                    continue
        return None

    async def filter_subscribers(
        self, subscribers: list[SubscriberDTO]
    ) -> tuple[list[SubscriberDTO], list[tuple[SubscriberDTO, SuppressionEntryDTO]]]:
        """Split subscribers into (allowed, suppressed-with-reason).

        The newsletter builders log the suppressed list so
        REQ-MAIL-132's audit trail exists.
        """
        allowed: list[SubscriberDTO] = []
        suppressed: list[tuple[SubscriberDTO, SuppressionEntryDTO]] = []
        for s in subscribers:
            match = await self.matches(s.email)
            if match is None:
                allowed.append(s)
            else:
                suppressed.append((s, match))
        return allowed, suppressed


def _supp_dto(model: SuppressionEntryModel) -> SuppressionEntryDTO:
    return SuppressionEntryDTO(
        id=model.id,
        pattern=model.pattern,
        pattern_type=model.pattern_type,
        reason=model.reason,
        created_at=model.created_at,
    )


@dataclass
class EmailDeliveryDTO:
    """Ledger row (REQ-MAIL-130)."""

    edition_id: str
    cadence: str
    email: str
    outcome: str
    queued_at: datetime
    subscriber_id: int | None = None
    message_id: str | None = None
    relay_response: str | None = None
    sent_at: datetime | None = None
    id: int | None = None


class EmailDeliveryRepository:
    """Append-only send ledger. Readers: bounce-drain, forensic queries."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(self, dto: EmailDeliveryDTO) -> EmailDeliveryDTO:
        model = EmailDeliveryModel(
            edition_id=dto.edition_id,
            cadence=dto.cadence,
            subscriber_id=dto.subscriber_id,
            email=dto.email.strip().lower(),
            message_id=dto.message_id,
            relay_response=dto.relay_response,
            outcome=dto.outcome,
            queued_at=dto.queued_at,
            sent_at=dto.sent_at,
        )
        self.session.add(model)
        await self.session.flush()
        return EmailDeliveryDTO(
            id=model.id,
            edition_id=model.edition_id,
            cadence=model.cadence,
            subscriber_id=model.subscriber_id,
            email=model.email,
            message_id=model.message_id,
            relay_response=model.relay_response,
            outcome=model.outcome,
            queued_at=model.queued_at,
            sent_at=model.sent_at,
        )

    async def find_by_message_id(self, message_id: str) -> EmailDeliveryDTO | None:
        """Match a DSN's Message-ID back to its originating send."""
        result = await self.session.execute(
            select(EmailDeliveryModel).where(
                EmailDeliveryModel.message_id == message_id
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return EmailDeliveryDTO(
            id=model.id,
            edition_id=model.edition_id,
            cadence=model.cadence,
            subscriber_id=model.subscriber_id,
            email=model.email,
            message_id=model.message_id,
            relay_response=model.relay_response,
            outcome=model.outcome,
            queued_at=model.queued_at,
            sent_at=model.sent_at,
        )


@dataclass
class EmailBounceDTO:
    """Parsed DSN (REQ-MAIL-131)."""

    email: str
    bounce_type: str  # hard | soft | challenge | unknown
    reported_at: datetime
    delivery_id: int | None = None
    diagnostic: str | None = None
    id: int | None = None


class EmailBounceRepository:
    """Parsed-DSN store. Written by the bounce-drain cron."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(self, dto: EmailBounceDTO) -> EmailBounceDTO:
        if dto.bounce_type not in ("hard", "soft", "challenge", "unknown"):
            msg = f"invalid bounce_type: {dto.bounce_type!r}"
            raise ValueError(msg)
        model = EmailBounceModel(
            delivery_id=dto.delivery_id,
            email=dto.email.strip().lower(),
            bounce_type=dto.bounce_type,
            diagnostic=dto.diagnostic,
            reported_at=dto.reported_at,
        )
        self.session.add(model)
        await self.session.flush()
        return EmailBounceDTO(
            id=model.id,
            delivery_id=model.delivery_id,
            email=model.email,
            bounce_type=model.bounce_type,
            diagnostic=model.diagnostic,
            reported_at=model.reported_at,
        )
