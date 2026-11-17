from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from baget_storage.infrastructure.postgres.base import Base
from baget_storage.infrastructure.postgres.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class UserOrm(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('manager', 'tenant')", name="valid_role"),
        CheckConstraint("token_version >= 0", name="token_version_nonnegative"),
        CheckConstraint("length(trim(full_name)) > 0", name="full_name_not_blank"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint("tax_id ~ '^([0-9]{10}|[0-9]{12})$'", name="valid_tax_id"),
    )

    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(16))
    company_name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(32))
    tax_id: Mapped[str | None] = mapped_column(String(12))
    notify_payments: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    notify_meters: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    notify_documents: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    notify_tickets: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    notify_leases: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    token_version: Mapped[int] = mapped_column(Integer, server_default=text("0"))
