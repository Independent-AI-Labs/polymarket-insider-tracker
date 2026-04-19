"""Phase F: subscribers + delivery ledger + bounce log + suppression list.

Revision ID: 004_phase_f
Revises: 003_phase_d
Create Date: 2026-04-19 03:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "004_phase_f"
down_revision: str | None = "003_phase_d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # CITEXT lets `UNIQUE(email)` match case-insensitively, which is the
    # operator-intuitive behaviour (RFC 5321 local-parts are technically
    # case-sensitive but no one actually relies on that).
    # pgcrypto gives us gen_random_uuid() for opt-in / unsubscribe tokens.
    # Both extensions are already widely available on managed Postgres.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS citext")
        op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        email_type = sa.dialects.postgresql.CITEXT()
        uuid_type = sa.dialects.postgresql.UUID(as_uuid=True)
    else:
        # SQLite test harness — use plain strings; the Python layer
        # lowercases before insert so uniqueness still holds.
        email_type = sa.String(320)
        uuid_type = sa.String(36)

    op.create_table(
        "subscribers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", email_type, nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        # SQLite has no ARRAY; store a comma-separated string and split
        # in the repo. Postgres would prefer TEXT[] but the repo layer
        # already needs to work with the SQLite shape for tests, so we
        # standardise on TEXT.
        sa.Column("cadences", sa.Text(), nullable=False, server_default="daily"),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="pending_opt_in",
        ),
        sa.Column("opt_in_token", uuid_type, nullable=False),
        sa.Column("opt_in_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unsubscribe_token", uuid_type, nullable=False),
        sa.Column("bounce_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_bounce_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_subscribers_email"),
        sa.CheckConstraint(
            "status IN ('pending_opt_in','active','bounced','unsubscribed','suppressed')",
            name="ck_subscribers_status",
        ),
    )
    op.create_index("idx_subscribers_status", "subscribers", ["status"])
    op.create_index(
        "idx_subscribers_opt_in_token", "subscribers", ["opt_in_token"]
    )
    op.create_index(
        "idx_subscribers_unsubscribe_token", "subscribers", ["unsubscribe_token"]
    )

    # Append-only send ledger (REQ-MAIL-130).
    op.create_table(
        "email_deliveries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("edition_id", sa.String(128), nullable=False),
        sa.Column("cadence", sa.String(32), nullable=False),
        sa.Column("subscriber_id", sa.Integer(), nullable=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("message_id", sa.String(256), nullable=True),
        sa.Column("relay_response", sa.String(32), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=False),  # sent|failed|suppressed|dry-run
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["subscriber_id"], ["subscribers.id"], ondelete="SET NULL"
        ),
    )
    op.create_index("idx_email_deliveries_edition", "email_deliveries", ["edition_id"])
    op.create_index(
        "idx_email_deliveries_subscriber", "email_deliveries", ["subscriber_id"]
    )
    op.create_index(
        "idx_email_deliveries_message_id", "email_deliveries", ["message_id"]
    )

    # Parsed DSNs (REQ-MAIL-131).
    op.create_table(
        "email_bounces",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("delivery_id", sa.Integer(), nullable=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column(
            "bounce_type",
            sa.String(16),
            nullable=False,  # hard | soft | challenge | unknown
        ),
        sa.Column("diagnostic", sa.Text(), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["delivery_id"], ["email_deliveries.id"], ondelete="SET NULL"
        ),
    )
    op.create_index("idx_email_bounces_email", "email_bounces", ["email"])

    # Suppression list (REQ-MAIL-116, 132).
    op.create_table(
        "suppression_list",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("pattern", sa.String(512), nullable=False),
        sa.Column(
            "pattern_type",
            sa.String(16),
            nullable=False,  # exact | domain | regex
        ),
        sa.Column("reason", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pattern", "pattern_type", name="uq_suppression_pattern"),
    )


def downgrade() -> None:
    op.drop_table("suppression_list")
    op.drop_index("idx_email_bounces_email", table_name="email_bounces")
    op.drop_table("email_bounces")
    op.drop_index("idx_email_deliveries_message_id", table_name="email_deliveries")
    op.drop_index("idx_email_deliveries_subscriber", table_name="email_deliveries")
    op.drop_index("idx_email_deliveries_edition", table_name="email_deliveries")
    op.drop_table("email_deliveries")
    op.drop_index("idx_subscribers_unsubscribe_token", table_name="subscribers")
    op.drop_index("idx_subscribers_opt_in_token", table_name="subscribers")
    op.drop_index("idx_subscribers_status", table_name="subscribers")
    op.drop_table("subscribers")
