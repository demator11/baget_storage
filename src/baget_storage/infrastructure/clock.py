from datetime import UTC, date, datetime


class UtcMeterClock:
    def today(self) -> date:
        return datetime.now(UTC).date()
