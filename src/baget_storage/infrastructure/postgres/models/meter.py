from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from baget_storage.domain.entities.meter import MeterKind
from baget_storage.infrastructure.postgres.base import Base
from baget_storage.infrastructure.postgres.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class MeterOrm(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "meters"
    __table_args__ = (
        CheckConstraint("kind IN ('electricity', 'water')", name="kind_valid"),
        CheckConstraint("initial_reading >= 0", name="initial_reading_nonnegative"),
        CheckConstraint("length(trim(serial_number)) > 0", name="serial_number_nonempty"),
    )

    room_id: Mapped[UUID] = mapped_column(ForeignKey("rooms.id", ondelete="RESTRICT"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    serial_number: Mapped[str] = mapped_column(String(100), unique=True)
    initial_reading: Mapped[Decimal] = mapped_column(Numeric(16, 3), default=Decimal("0"))

    @property
    def unit(self) -> str:
        return MeterKind(self.kind).unit


class MeterReadingOrm(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "meter_readings"
    __table_args__ = (
        UniqueConstraint("meter_id", "period", name="uq_meter_readings_meter_period"),
        CheckConstraint("status IN ('pending', 'accepted', 'rejected')", name="status_valid"),
        CheckConstraint("previous_value >= 0 AND value >= previous_value", name="value_valid"),
        CheckConstraint("EXTRACT(DAY FROM period) = 1", name="period_month_start"),
        CheckConstraint(
            "(photo_content IS NULL AND photo_size IS NULL) OR "
            "(photo_content IS NOT NULL AND photo_size = octet_length(photo_content) "
            "AND photo_size > 0 AND photo_size <= 8388608)",
            name="photo_consistent",
        ),
        CheckConstraint(
            "(status = 'pending' AND reviewed_by IS NULL AND reviewed_at IS NULL "
            "AND rejection_reason IS NULL) OR "
            "(status = 'accepted' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL "
            "AND rejection_reason IS NULL) OR "
            "(status = 'rejected' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL "
            "AND rejection_reason IS NOT NULL AND length(trim(rejection_reason)) > 0)",
            name="review_consistent",
        ),
        CheckConstraint(
            "correction_reason IS NULL OR "
            "(status = 'accepted' AND length(trim(correction_reason)) > 0)",
            name="correction_consistent",
        ),
    )

    meter_id: Mapped[UUID] = mapped_column(ForeignKey("meters.id", ondelete="RESTRICT"), index=True)
    submitted_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    period: Mapped[date] = mapped_column(Date)
    value: Mapped[Decimal] = mapped_column(Numeric(16, 3))
    previous_value: Mapped[Decimal] = mapped_column(Numeric(16, 3))
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    rejection_reason: Mapped[str | None] = mapped_column(String(1000))
    correction_reason: Mapped[str | None] = mapped_column(String(1000))
    reviewed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    photo_content: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    photo_size: Mapped[int | None] = mapped_column(Integer)

    @property
    def has_photo(self) -> bool:
        return self.photo_size is not None

    @property
    def consumption(self) -> Decimal:
        return self.value - self.previous_value
