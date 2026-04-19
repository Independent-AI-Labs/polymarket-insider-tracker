"""Phase D persistence gaps: sniper clusters + alert daily rollup.

Revision ID: 003_phase_d
Revises: 002_detector_metrics
Create Date: 2026-04-19 02:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "003_phase_d"
down_revision: str | None = "002_detector_metrics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Sniper clusters — persists what SniperDetector.run_clustering
    # currently throws away after emitting SniperClusterSignal.
    op.create_table(
        "sniper_clusters",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cluster_id", sa.String(64), nullable=False),
        sa.Column("wallet_count", sa.Integer(), nullable=False),
        sa.Column("avg_entry_delta_seconds", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        # JSON array of market_ids the cluster shares in common.
        sa.Column("markets_in_common", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_sniper_clusters_detected_at",
        "sniper_clusters",
        ["detected_at"],
    )
    op.create_index(
        "idx_sniper_clusters_cluster_id",
        "sniper_clusters",
        ["cluster_id"],
    )

    op.create_table(
        "sniper_cluster_members",
        sa.Column("cluster_row_id", sa.Integer(), nullable=False),
        sa.Column("wallet_address", sa.String(42), nullable=False),
        sa.ForeignKeyConstraint(
            ["cluster_row_id"], ["sniper_clusters.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("cluster_row_id", "wallet_address"),
    )
    op.create_index(
        "idx_sniper_cluster_members_wallet",
        "sniper_cluster_members",
        ["wallet_address"],
    )

    # Alert daily roll-up — the newsletter reads this instead of scanning
    # Redis alert indices each run.
    op.create_table(
        "alert_daily_rollup",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("market_id", sa.String(66), nullable=False),
        sa.Column("signal", sa.String(32), nullable=False),
        sa.Column("alert_count", sa.Integer(), nullable=False),
        sa.Column("unique_wallets", sa.Integer(), nullable=False),
        sa.Column("total_notional", sa.Numeric(18, 4), nullable=True),
        sa.PrimaryKeyConstraint("day", "market_id", "signal"),
    )
    op.create_index(
        "idx_alert_daily_rollup_day",
        "alert_daily_rollup",
        ["day"],
    )


def downgrade() -> None:
    op.drop_index("idx_alert_daily_rollup_day", table_name="alert_daily_rollup")
    op.drop_table("alert_daily_rollup")
    op.drop_index("idx_sniper_cluster_members_wallet", table_name="sniper_cluster_members")
    op.drop_table("sniper_cluster_members")
    op.drop_index("idx_sniper_clusters_cluster_id", table_name="sniper_clusters")
    op.drop_index("idx_sniper_clusters_detected_at", table_name="sniper_clusters")
    op.drop_table("sniper_clusters")
