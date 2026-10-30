from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from baget_storage.core.exceptions import BusinessRuleViolation


class RoomStatus(StrEnum):
    AVAILABLE = "available"
    OCCUPIED = "occupied"
    MAINTENANCE = "maintenance"


class LeaseStatus(StrEnum):
    ACTIVE = "active"
    TERMINATED = "terminated"


@dataclass(frozen=True, slots=True)
class LeasePeriod:
    start_date: date
    end_date: date

    def __post_init__(self) -> None:
        if self.end_date < self.start_date:
            raise BusinessRuleViolation("Lease end date must not precede its start date.")

    def overlaps(self, other: LeasePeriod) -> bool:
        return self.start_date <= other.end_date and other.start_date <= self.end_date


def validate_monthly_rent(value: Decimal) -> None:
    if not value.is_finite() or value < 0:
        raise BusinessRuleViolation("Monthly rent must be a finite nonnegative amount.")


def validate_room_status(status: RoomStatus) -> None:
    if status == RoomStatus.OCCUPIED:
        raise BusinessRuleViolation("Occupied status is determined by the current lease.")
