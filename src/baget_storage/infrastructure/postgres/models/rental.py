from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from baget_storage.infrastructure.postgres.base import Base
from baget_storage.infrastructure.postgres.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class RoomOrm(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "rooms"
    __table_args__ = (
        UniqueConstraint("building", "name", name="uq_rooms_building_name"),
        CheckConstraint("area_m2 > 0", name="positive_area"),
        CheckConstraint("monthly_rent >= 0", name="nonnegative_rent"),
        CheckConstraint("ceiling_height_m > 0", name="positive_ceiling_height"),
        CheckConstraint("status IN ('available', 'maintenance')", name="valid_status"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        CheckConstraint("length(trim(building)) > 0", name="building_not_blank"),
    )

    name: Mapped[str] = mapped_column(String(160))
    area_m2: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    building: Mapped[str] = mapped_column(String(160))
    floor: Mapped[int]
    monthly_rent: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(16), server_default="available")
    ceiling_height_m: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    gate_type: Mapped[str | None] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(String(5000))


class LeaseOrm(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "leases"

    number: Mapped[str] = mapped_column(String(80), unique=True)
    room_id: Mapped[UUID] = mapped_column(ForeignKey("rooms.id", ondelete="RESTRICT"), index=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    monthly_rent: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(16), server_default="active")

    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="ordered_dates"),
        CheckConstraint("monthly_rent >= 0", name="nonnegative_rent"),
        CheckConstraint("status IN ('active', 'terminated')", name="valid_status"),
        CheckConstraint("length(trim(number)) > 0", name="number_not_blank"),
        ExcludeConstraint(
            (room_id, "="),
            (func.daterange(start_date, end_date, "[]"), "&&"),
            where=text("status = 'active'"),
            using="gist",
            name="ex_leases_room_active_period",
        ),
    )
