"""SQLAlchemy models for persistent storage.

This module defines the database schema for storing wallet profiles,
funding transfers, and wallet relationships.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

if TYPE_CHECKING:
    pass


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


class WalletProfileModel(Base):
    """SQLAlchemy model for wallet profiles.

    Stores analyzed wallet information including age, transaction count,
    balances, and freshness classification.
    """

    __tablename__ = "wallet_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(String(42), unique=True, nullable=False)
    nonce: Mapped[int] = mapped_column(Integer, nullable=False)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_fresh: Mapped[bool] = mapped_column(Boolean, nullable=False)
    matic_balance: Mapped[Decimal | None] = mapped_column(Numeric(30, 0), nullable=True)
    usdc_balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (Index("idx_wallet_profiles_address", "address"),)


class FundingTransferModel(Base):
    """SQLAlchemy model for funding transfers.

    Stores ERC20 transfer events to track wallet funding sources.
    """

    __tablename__ = "funding_transfers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_address: Mapped[str] = mapped_column(String(42), nullable=False)
    to_address: Mapped[str] = mapped_column(String(42), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(30, 6), nullable=False)
    token: Mapped[str] = mapped_column(String(10), nullable=False)
    tx_hash: Mapped[str] = mapped_column(String(66), unique=True, nullable=False)
    block_number: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("idx_funding_transfers_to", "to_address"),
        Index("idx_funding_transfers_from", "from_address"),
        Index("idx_funding_transfers_block", "block_number"),
    )


class WalletRelationshipModel(Base):
    """SQLAlchemy model for wallet relationships.

    Stores graph edges between wallets representing funding relationships
    or entity linkages.
    """

    __tablename__ = "wallet_relationships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet_a: Mapped[str] = mapped_column(String(42), nullable=False)
    wallet_b: Mapped[str] = mapped_column(String(42), nullable=False)
    relationship_type: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        UniqueConstraint(
            "wallet_a", "wallet_b", "relationship_type", name="uq_wallet_relationship"
        ),
        Index("idx_wallet_relationships_a", "wallet_a"),
        Index("idx_wallet_relationships_b", "wallet_b"),
    )


class DetectorMetricsModel(Base):
    """SQLAlchemy model for detector-metrics rows.

    One row per (signal, window) written after a backtest replay. The
    monthly newsletter reads these rows verbatim; the weekly hit/miss
    retrospective reads the `hits`/`misses` columns.
    """

    __tablename__ = "detector_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    signal: Mapped[str] = mapped_column(String(32), nullable=False)
    alerts_total: Mapped[int] = mapped_column(Integer, nullable=False)
    hits: Mapped[int] = mapped_column(Integer, nullable=False)
    misses: Mapped[int] = mapped_column(Integer, nullable=False)
    pending: Mapped[int] = mapped_column(Integer, nullable=False)
    precision: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    pnl_uplift_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_detector_metrics_window_signal", "window_start", "signal"),
    )


class SniperClusterModel(Base):
    """Persisted output of `SniperDetector.run_clustering`.

    One row per cluster detected in a given run. Membership lives in
    `sniper_cluster_members` so we can query "which wallets share
    clusters with wallet X" without parsing JSON blobs.
    """

    __tablename__ = "sniper_clusters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    cluster_id: Mapped[str] = mapped_column(String(64), nullable=False)
    wallet_count: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_entry_delta_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    # Ordered list of market_ids the cluster members share. JSON over
    # ARRAY so SQLite (test harness) works identically to Postgres.
    markets_in_common: Mapped[list[str]] = mapped_column(JSON, nullable=False)

    __table_args__ = (
        Index("idx_sniper_clusters_detected_at", "detected_at"),
        Index("idx_sniper_clusters_cluster_id", "cluster_id"),
    )


class SniperClusterMemberModel(Base):
    """Wallet ↔ cluster-row membership row."""

    __tablename__ = "sniper_cluster_members"

    cluster_row_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sniper_clusters.id", ondelete="CASCADE"), primary_key=True
    )
    wallet_address: Mapped[str] = mapped_column(String(42), primary_key=True)

    __table_args__ = (
        Index("idx_sniper_cluster_members_wallet", "wallet_address"),
    )


class AlertDailyRollupModel(Base):
    """Per-day, per-market, per-signal alert aggregate.

    Written by `scripts/compute-daily-rollup.py` at 00:05 UTC so the
    daily newsletter doesn't have to scan the Redis alert indices on
    every run.
    """

    __tablename__ = "alert_daily_rollup"

    day: Mapped[datetime] = mapped_column(Date, primary_key=True)
    market_id: Mapped[str] = mapped_column(String(66), primary_key=True)
    signal: Mapped[str] = mapped_column(String(32), primary_key=True)
    alert_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unique_wallets: Mapped[int] = mapped_column(Integer, nullable=False)
    total_notional: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    __table_args__ = (Index("idx_alert_daily_rollup_day", "day"),)
