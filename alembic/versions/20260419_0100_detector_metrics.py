"""Detector metrics for the backtesting harness.

Revision ID: 002_detector_metrics
Revises: 001_initial
Create Date: 2026-04-19 01:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "002_detector_metrics"
down_revision: str | None = "001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "detector_metrics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signal", sa.String(32), nullable=False),
        sa.Column("alerts_total", sa.Integer(), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.Column("misses", sa.Integer(), nullable=False),
        sa.Column("pending", sa.Integer(), nullable=False),
        sa.Column("precision", sa.Numeric(5, 4), nullable=True),
        sa.Column("pnl_uplift_bps", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_detector_metrics_window_signal",
        "detector_metrics",
        ["window_start", "signal"],
    )


def downgrade() -> None:
    op.drop_index("idx_detector_metrics_window_signal", table_name="detector_metrics")
    op.drop_table("detector_metrics")
