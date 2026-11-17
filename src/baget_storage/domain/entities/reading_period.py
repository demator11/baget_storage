from dataclasses import dataclass
from datetime import date

from baget_storage.core.exceptions import BusinessRuleViolation
from baget_storage.schemas.meter import ReadingPeriodDTO


@dataclass(frozen=True, slots=True)
class ReadingPeriodPolicy:
    opening_day: int = 1
    deadline_day: int = 5

    def __post_init__(self) -> None:
        if not 1 <= self.opening_day <= 28 or not 1 <= self.deadline_day <= 28:
            raise ValueError("Reading window days must be between 1 and 28")

    def window(self, period: date, today: date) -> ReadingPeriodDTO:
        if period.day != 1 or period.year >= 9999:
            raise BusinessRuleViolation("A reading period must be the first day of a month.")
        following = date(period.year + period.month // 12, period.month % 12 + 1, 1)
        opens_on = period.replace(day=self.opening_day)
        closes_on = following.replace(day=self.deadline_day)
        return ReadingPeriodDTO(
            period=period,
            opens_on=opens_on,
            closes_on=closes_on,
            is_open=opens_on <= today <= closes_on,
        )
