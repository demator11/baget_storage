"""Complete application screen workflows"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "717ed1b09b5e"
down_revision: str | Sequence[str] | None = "d35d8b8c97ce"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.create_table(
        "email_outbox",
        sa.Column("recipient", sa.String(length=254), nullable=False),
        sa.Column("template", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("deduplication_key", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("claim_id", sa.Uuid(), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'sent', 'failed', 'expired')",
            name=op.f("ck_email_outbox_valid_status"),
        ),
        sa.CheckConstraint(
            "template IN ('notification', 'verify_email', 'password_reset')",
            name=op.f("ck_email_outbox_valid_template"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_email_outbox_attempts_nonnegative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_outbox")),
        sa.UniqueConstraint("deduplication_key", name=op.f("uq_email_outbox_deduplication_key")),
    )
    op.create_index(
        "ix_email_outbox_delivery", "email_outbox", ["status", "available_at"], unique=False
    )
    op.create_table(
        "auth_sessions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "token_version >= 0", name=op.f("ck_auth_sessions_token_version_nonnegative")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_auth_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
    )
    op.create_index(
        op.f("ix_auth_sessions_expires_at"), "auth_sessions", ["expires_at"], unique=False
    )
    op.create_index(op.f("ix_auth_sessions_user_id"), "auth_sessions", ["user_id"], unique=False)
    op.create_table(
        "email_action_tokens",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=24), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "purpose IN ('verify_email', 'password_reset')",
            name=op.f("ck_email_action_tokens_valid_purpose"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_email_action_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_action_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_email_action_tokens_token_hash")),
    )
    op.create_index(
        op.f("ix_email_action_tokens_expires_at"),
        "email_action_tokens",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_action_tokens_user_id"), "email_action_tokens", ["user_id"], unique=False
    )
    op.create_table(
        "refresh_tokens",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["auth_sessions.id"],
            name=op.f("fk_refresh_tokens_session_id_auth_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )
    op.create_index(
        op.f("ix_refresh_tokens_session_id"), "refresh_tokens", ["session_id"], unique=False
    )
    op.add_column("meter_readings", sa.Column("photo_content", sa.LargeBinary(), nullable=True))
    op.add_column("meter_readings", sa.Column("photo_size", sa.Integer(), nullable=True))
    op.create_check_constraint(
        op.f("ck_meter_readings_photo_consistent"),
        "meter_readings",
        "(photo_content IS NULL AND photo_size IS NULL) OR (photo_content IS NOT NULL AND photo_size = octet_length(photo_content) AND photo_size > 0 AND photo_size <= 8388608)",
    )
    op.add_column("notifications", sa.Column("resource_type", sa.String(length=16), nullable=True))
    op.add_column("notifications", sa.Column("resource_id", sa.Uuid(), nullable=True))
    op.add_column(
        "notifications", sa.Column("deduplication_key", sa.String(length=200), nullable=True)
    )
    op.drop_constraint(op.f("ck_notifications_valid_category"), "notifications", type_="check")
    op.create_check_constraint(
        op.f("ck_notifications_valid_event_category"),
        "notifications",
        "category IN ('system', 'payments', 'documents', 'meters', 'tickets', 'leases')",
    )
    op.create_check_constraint(
        op.f("ck_notifications_valid_resource"),
        "notifications",
        "(resource_type IS NULL AND resource_id IS NULL) OR (resource_type IS NOT NULL AND resource_id IS NOT NULL AND resource_type IN ('invoice', 'document', 'ticket', 'lease', 'meter', 'reading'))",
    )
    op.create_unique_constraint(
        op.f("uq_notifications_deduplication_key"), "notifications", ["deduplication_key"]
    )
    op.add_column(
        "tickets",
        sa.Column("public_number", sa.Integer(), sa.Identity(always=False), nullable=False),
    )
    op.create_unique_constraint(op.f("uq_tickets_public_number"), "tickets", ["public_number"])
    op.add_column(
        "users",
        sa.Column("notify_tickets", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("notify_leases", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.add_column(
        "users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:

    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "notify_leases")
    op.drop_column("users", "notify_tickets")
    op.drop_constraint(op.f("uq_tickets_public_number"), "tickets", type_="unique")
    op.drop_column("tickets", "public_number")
    op.drop_constraint(op.f("uq_notifications_deduplication_key"), "notifications", type_="unique")
    op.drop_constraint(op.f("ck_notifications_valid_resource"), "notifications", type_="check")
    op.drop_constraint(
        op.f("ck_notifications_valid_event_category"), "notifications", type_="check"
    )
    op.execute(
        "UPDATE notifications SET category = 'system' WHERE category IN ('tickets', 'leases')"
    )
    op.create_check_constraint(
        op.f("ck_notifications_valid_category"),
        "notifications",
        "category::text = ANY (ARRAY['system'::character varying, 'payments'::character varying, 'documents'::character varying, 'meters'::character varying]::text[])",
    )
    op.drop_column("notifications", "deduplication_key")
    op.drop_column("notifications", "resource_id")
    op.drop_column("notifications", "resource_type")
    op.drop_constraint(op.f("ck_meter_readings_photo_consistent"), "meter_readings", type_="check")
    op.drop_column("meter_readings", "photo_size")
    op.drop_column("meter_readings", "photo_content")
    op.drop_index(op.f("ix_refresh_tokens_session_id"), table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index(op.f("ix_email_action_tokens_user_id"), table_name="email_action_tokens")
    op.drop_index(op.f("ix_email_action_tokens_expires_at"), table_name="email_action_tokens")
    op.drop_table("email_action_tokens")
    op.drop_index(op.f("ix_auth_sessions_user_id"), table_name="auth_sessions")
    op.drop_index(op.f("ix_auth_sessions_expires_at"), table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ix_email_outbox_delivery", table_name="email_outbox")
    op.drop_table("email_outbox")
