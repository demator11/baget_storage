from datetime import date
from decimal import Decimal
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from baget_storage.domain.ports.office import NotificationRepository
from baget_storage.schemas.meter import (
    CorrectReading,
    CreateMeter,
    CreateReading,
    MeterDTO,
    MeterFilters,
    MeterPage,
    MeterStatsDTO,
    ReadingDTO,
    ReadingFilters,
    ReadingPage,
    ReadingPhotoDTO,
    ReviewReading,
)


class MeterClock(Protocol):
    def today(self) -> date: ...


class MeterPhotoProcessor(Protocol):
    async def process(self, content: bytes) -> ReadingPhotoDTO: ...


class MeterRepository(Protocol):
    async def create(self, data: CreateMeter) -> MeterDTO: ...

    async def get_by_id(self, meter_id: UUID, *, lock: bool = False) -> MeterDTO | None: ...

    async def room_exists(self, room_id: UUID) -> bool: ...

    async def tenant_has_access(
        self, room_id: UUID, tenant_id: UUID, today: date, period: date | None = None
    ) -> bool: ...

    async def list_meters(
        self, filters: MeterFilters, tenant_id: UUID | None, today: date
    ) -> MeterPage: ...

    async def latest_reading(self, meter_id: UUID) -> ReadingDTO | None: ...

    async def get_reading(self, reading_id: UUID) -> ReadingDTO | None: ...

    async def save_photo(self, reading_id: UUID, photo: ReadingPhotoDTO) -> ReadingDTO: ...

    async def get_photo(self, reading_id: UUID) -> ReadingPhotoDTO | None: ...

    async def create_reading(self, data: CreateReading) -> ReadingDTO: ...

    async def resubmit_reading(self, reading_id: UUID, value: Decimal) -> ReadingDTO: ...

    async def review_reading(
        self, reading_id: UUID, reviewer_id: UUID, data: ReviewReading
    ) -> ReadingDTO: ...

    async def correct_reading(
        self, reading_id: UUID, reviewer_id: UUID, data: CorrectReading
    ) -> ReadingDTO: ...

    async def list_readings(
        self, meter_id: UUID | None, filters: ReadingFilters, tenant_id: UUID | None = None
    ) -> ReadingPage: ...

    async def stats(
        self, filters: MeterFilters, tenant_id: UUID | None, today: date
    ) -> MeterStatsDTO: ...

    async def periods(
        self, tenant_id: UUID | None, today: date, limit: int, offset: int
    ) -> tuple[list[date], int]: ...


class MeterUnitOfWork(Protocol):
    meters: MeterRepository
    notifications: NotificationRepository

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...
