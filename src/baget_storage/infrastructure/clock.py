from datetime import UTC, date, datetime


class UtcMeterClock:
    def today(self) -> date:
        return datetime.now(UTC).date()


class UtcWorkerClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
