"""Link invoice documents and audit reading corrections"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d35d8b8c97ce"
down_revision: str | Sequence[str] | None = "4b6098bc7f3a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.add_column("invoices", sa.Column("document_id", sa.Uuid(), nullable=True))
    op.create_unique_constraint(op.f("uq_invoices_document_id"), "invoices", ["document_id"])
    op.create_foreign_key(
        op.f("fk_invoices_document_id_documents"),
        "invoices",
        "documents",
        ["document_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "meter_readings", sa.Column("correction_reason", sa.String(length=1000), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_meter_readings_correction_consistent"),
        "meter_readings",
        "correction_reason IS NULL OR (status = 'accepted' AND length(trim(correction_reason)) > 0)",
    )


def downgrade() -> None:

    op.drop_constraint(
        op.f("ck_meter_readings_correction_consistent"), "meter_readings", type_="check"
    )
    op.drop_column("meter_readings", "correction_reason")
    op.drop_constraint(op.f("fk_invoices_document_id_documents"), "invoices", type_="foreignkey")
    op.drop_constraint(op.f("uq_invoices_document_id"), "invoices", type_="unique")
    op.drop_column("invoices", "document_id")
