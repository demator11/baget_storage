"""Create warehouse rental schema"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "4b6098bc7f3a"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table(
        "rooms",
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("area_m2", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("building", sa.String(length=160), nullable=False),
        sa.Column("floor", sa.Integer(), nullable=False),
        sa.Column("monthly_rent", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="available", nullable=False),
        sa.Column("ceiling_height_m", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("gate_type", sa.String(length=160), nullable=True),
        sa.Column("description", sa.String(length=5000), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('available', 'maintenance')", name=op.f("ck_rooms_valid_status")
        ),
        sa.CheckConstraint("area_m2 > 0", name=op.f("ck_rooms_positive_area")),
        sa.CheckConstraint("ceiling_height_m > 0", name=op.f("ck_rooms_positive_ceiling_height")),
        sa.CheckConstraint("length(trim(building)) > 0", name=op.f("ck_rooms_building_not_blank")),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_rooms_name_not_blank")),
        sa.CheckConstraint("monthly_rent >= 0", name=op.f("ck_rooms_nonnegative_rent")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rooms")),
        sa.UniqueConstraint("building", "name", name="uq_rooms_building_name"),
    )
    op.create_table(
        "users",
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("company_name", sa.String(length=200), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("tax_id", sa.String(length=12), nullable=True),
        sa.Column("notify_payments", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("notify_meters", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("notify_documents", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("token_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('manager', 'tenant')", name=op.f("ck_users_valid_role")),
        sa.CheckConstraint(
            "tax_id ~ '^([0-9]{10}|[0-9]{12})$'", name=op.f("ck_users_valid_tax_id")
        ),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_users_email_lowercase")),
        sa.CheckConstraint(
            "length(trim(full_name)) > 0", name=op.f("ck_users_full_name_not_blank")
        ),
        sa.CheckConstraint("token_version >= 0", name=op.f("ck_users_token_version_nonnegative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "leases",
        sa.Column("number", sa.String(length=80), nullable=False),
        sa.Column("room_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("monthly_rent", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        postgresql.ExcludeConstraint(
            (sa.column("room_id"), "="),
            (sa.text("daterange(start_date, end_date, '[]')"), "&&"),
            where=sa.text("status = 'active'"),
            using="gist",
            name="ex_leases_room_active_period",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'terminated')", name=op.f("ck_leases_valid_status")
        ),
        sa.CheckConstraint("end_date >= start_date", name=op.f("ck_leases_ordered_dates")),
        sa.CheckConstraint("length(trim(number)) > 0", name=op.f("ck_leases_number_not_blank")),
        sa.CheckConstraint("monthly_rent >= 0", name=op.f("ck_leases_nonnegative_rent")),
        sa.ForeignKeyConstraint(
            ["room_id"], ["rooms.id"], name=op.f("fk_leases_room_id_rooms"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["users.id"], name=op.f("fk_leases_tenant_id_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leases")),
        sa.UniqueConstraint("number", name=op.f("uq_leases_number")),
    )
    op.create_index(op.f("ix_leases_room_id"), "leases", ["room_id"], unique=False)
    op.create_index(op.f("ix_leases_tenant_id"), "leases", ["tenant_id"], unique=False)
    op.create_table(
        "meters",
        sa.Column("room_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("serial_number", sa.String(length=100), nullable=False),
        sa.Column("initial_reading", sa.Numeric(precision=16, scale=3), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('electricity', 'water')", name=op.f("ck_meters_kind_valid")),
        sa.CheckConstraint(
            "initial_reading >= 0", name=op.f("ck_meters_initial_reading_nonnegative")
        ),
        sa.CheckConstraint(
            "length(trim(serial_number)) > 0", name=op.f("ck_meters_serial_number_nonempty")
        ),
        sa.ForeignKeyConstraint(
            ["room_id"], ["rooms.id"], name=op.f("fk_meters_room_id_rooms"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meters")),
        sa.UniqueConstraint("serial_number", name=op.f("uq_meters_serial_number")),
    )
    op.create_index(op.f("ix_meters_room_id"), "meters", ["room_id"], unique=False)
    op.create_table(
        "notifications",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.String(length=2000), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "category IN ('system', 'payments', 'documents', 'meters')",
            name=op.f("ck_notifications_valid_category"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notifications_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
    )
    op.create_index(op.f("ix_notifications_user_id"), "notifications", ["user_id"], unique=False)
    op.create_table(
        "tickets",
        sa.Column("room_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=5000), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="new", nullable=False),
        sa.Column("assignee", sa.String(length=200), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('new', 'in_progress', 'closed')", name=op.f("ck_tickets_valid_status")
        ),
        sa.CheckConstraint(
            "length(trim(description)) > 0", name=op.f("ck_tickets_description_not_blank")
        ),
        sa.CheckConstraint("length(trim(title)) > 0", name=op.f("ck_tickets_title_not_blank")),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_tickets_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["room_id"], ["rooms.id"], name=op.f("fk_tickets_room_id_rooms"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tickets")),
    )
    op.create_index(op.f("ix_tickets_created_by"), "tickets", ["created_by"], unique=False)
    op.create_index(op.f("ix_tickets_room_id"), "tickets", ["room_id"], unique=False)
    op.create_index(op.f("ix_tickets_status"), "tickets", ["status"], unique=False)
    op.create_table(
        "documents",
        sa.Column("lease_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("issued_on", sa.Date(), nullable=False),
        sa.Column("filename", sa.String(length=200), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('contract', 'invoice', 'act')", name=op.f("ck_documents_valid_kind")
        ),
        sa.CheckConstraint(
            "status IN ('available', 'active', 'signed')", name=op.f("ck_documents_valid_status")
        ),
        sa.CheckConstraint(
            "octet_length(content) = size_bytes", name=op.f("ck_documents_content_size_matches")
        ),
        sa.CheckConstraint(
            "size_bytes > 0 AND size_bytes <= 10485760", name=op.f("ck_documents_valid_size")
        ),
        sa.ForeignKeyConstraint(
            ["lease_id"],
            ["leases.id"],
            name=op.f("fk_documents_lease_id_leases"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
    )
    op.create_index(op.f("ix_documents_kind"), "documents", ["kind"], unique=False)
    op.create_index(op.f("ix_documents_lease_id"), "documents", ["lease_id"], unique=False)
    op.create_table(
        "invoices",
        sa.Column("lease_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("issued_on", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_invoices_positive_amount")),
        sa.CheckConstraint("due_date >= issued_on", name=op.f("ck_invoices_ordered_dates")),
        sa.CheckConstraint("length(trim(number)) > 0", name=op.f("ck_invoices_number_not_blank")),
        sa.ForeignKeyConstraint(
            ["lease_id"],
            ["leases.id"],
            name=op.f("fk_invoices_lease_id_leases"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoices")),
        sa.UniqueConstraint("number", name=op.f("uq_invoices_number")),
    )
    op.create_index(op.f("ix_invoices_due_date"), "invoices", ["due_date"], unique=False)
    op.create_index(op.f("ix_invoices_lease_id"), "invoices", ["lease_id"], unique=False)
    op.create_table(
        "meter_readings",
        sa.Column("meter_id", sa.Uuid(), nullable=False),
        sa.Column("submitted_by", sa.Uuid(), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(precision=16, scale=3), nullable=False),
        sa.Column("previous_value", sa.Numeric(precision=16, scale=3), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("rejection_reason", sa.String(length=1000), nullable=True),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND reviewed_by IS NULL AND reviewed_at IS NULL AND rejection_reason IS NULL) OR (status = 'accepted' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL AND rejection_reason IS NULL) OR (status = 'rejected' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL AND rejection_reason IS NOT NULL AND length(trim(rejection_reason)) > 0)",
            name=op.f("ck_meter_readings_review_consistent"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected')",
            name=op.f("ck_meter_readings_status_valid"),
        ),
        sa.CheckConstraint(
            "EXTRACT(DAY FROM period) = 1", name=op.f("ck_meter_readings_period_month_start")
        ),
        sa.CheckConstraint(
            "previous_value >= 0 AND value >= previous_value",
            name=op.f("ck_meter_readings_value_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["meter_id"],
            ["meters.id"],
            name=op.f("fk_meter_readings_meter_id_meters"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.id"],
            name=op.f("fk_meter_readings_reviewed_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by"],
            ["users.id"],
            name=op.f("fk_meter_readings_submitted_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meter_readings")),
        sa.UniqueConstraint("meter_id", "period", name="uq_meter_readings_meter_period"),
    )
    op.create_index(
        op.f("ix_meter_readings_meter_id"), "meter_readings", ["meter_id"], unique=False
    )
    op.create_index(
        op.f("ix_meter_readings_submitted_by"), "meter_readings", ["submitted_by"], unique=False
    )
    op.create_table(
        "ticket_events",
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_ticket_events_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["tickets.id"],
            name=op.f("fk_ticket_events_ticket_id_tickets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket_events")),
    )
    op.create_index(
        op.f("ix_ticket_events_ticket_id"), "ticket_events", ["ticket_id"], unique=False
    )
    op.create_table(
        "payments",
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("reference", sa.String(length=150), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("amount > 0", name=op.f("ck_payments_positive_amount")),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
            name=op.f("fk_payments_invoice_id_invoices"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
        sa.UniqueConstraint("reference", name=op.f("uq_payments_reference")),
    )
    op.create_index(op.f("ix_payments_invoice_id"), "payments", ["invoice_id"], unique=False)


def downgrade() -> None:

    op.drop_index(op.f("ix_payments_invoice_id"), table_name="payments")
    op.drop_table("payments")
    op.drop_index(op.f("ix_ticket_events_ticket_id"), table_name="ticket_events")
    op.drop_table("ticket_events")
    op.drop_index(op.f("ix_meter_readings_submitted_by"), table_name="meter_readings")
    op.drop_index(op.f("ix_meter_readings_meter_id"), table_name="meter_readings")
    op.drop_table("meter_readings")
    op.drop_index(op.f("ix_invoices_lease_id"), table_name="invoices")
    op.drop_index(op.f("ix_invoices_due_date"), table_name="invoices")
    op.drop_table("invoices")
    op.drop_index(op.f("ix_documents_lease_id"), table_name="documents")
    op.drop_index(op.f("ix_documents_kind"), table_name="documents")
    op.drop_table("documents")
    op.drop_index(op.f("ix_tickets_status"), table_name="tickets")
    op.drop_index(op.f("ix_tickets_room_id"), table_name="tickets")
    op.drop_index(op.f("ix_tickets_created_by"), table_name="tickets")
    op.drop_table("tickets")
    op.drop_index(op.f("ix_notifications_user_id"), table_name="notifications")
    op.drop_table("notifications")
    op.drop_index(op.f("ix_meters_room_id"), table_name="meters")
    op.drop_table("meters")
    op.drop_index(op.f("ix_leases_tenant_id"), table_name="leases")
    op.drop_index(op.f("ix_leases_room_id"), table_name="leases")
    op.drop_table("leases")
    op.drop_table("users")
    op.drop_table("rooms")
