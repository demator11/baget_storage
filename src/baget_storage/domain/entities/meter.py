from datetime import date
from decimal import Decimal
from enum import StrEnum

from baget_storage.core.exceptions import BusinessRuleViolation


class MeterKind(StrEnum):
    ELECTRICITY = "electricity"
    WATER = "water"

    @property
    def unit(self) -> str:
        return "kwh" if self is MeterKind.ELECTRICITY else "m3"


class ReadingStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


def validate_reading(period: date, value: Decimal, previous: Decimal, today: date) -> None:
    if period.day != 1:
        raise BusinessRuleViolation("Reading period must be the first day of a month.")
    if period > today.replace(day=1):
        raise BusinessRuleViolation("Readings cannot be submitted for a future month.")
    if not value.is_finite() or value < previous:
        raise BusinessRuleViolation("Reading must be finite and not less than the previous value.")


def validate_review(status: ReadingStatus, reason: str | None) -> None:
    if status not in (ReadingStatus.ACCEPTED, ReadingStatus.REJECTED):
        raise BusinessRuleViolation("A reading can only be accepted or rejected.")
    if status is ReadingStatus.REJECTED and not (reason and reason.strip()):
        raise BusinessRuleViolation("A rejection reason is required.")
