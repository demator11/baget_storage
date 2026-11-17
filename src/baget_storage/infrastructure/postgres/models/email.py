from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from baget_storage.infrastructure.postgres.base import Base
from baget_storage.infrastructure.postgres.mixins import UUIDPrimaryKeyMixin


class EmailOutboxOrm(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "email_outbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'sent', 'failed', 'expired')", name="valid_status"
        ),
        CheckConstraint(
            "template IN ('notification', 'verify_email', 'password_reset')", name="valid_template"
        ),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        Index("ix_email_outbox_delivery", "status", "available_at"),
    )

    recipient: Mapped[str] = mapped_column(String(254))
    template: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, str]] = mapped_column(JSONB)
    deduplication_key: Mapped[str] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    claim_id: Mapped[UUID | None]
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
