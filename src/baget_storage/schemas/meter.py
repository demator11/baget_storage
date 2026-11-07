from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from baget_storage.domain.entities.meter import MeterKind, ReadingStatus

ReadingValue = Annotated[Decimal, Field(ge=0, max_digits=16, decimal_places=3)]


class MeterInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateMeter(MeterInput):
    room_id: UUID
    kind: MeterKind
    serial_number: str = Field(min_length=1, max_length=100)
    initial_reading: ReadingValue = Decimal("0")


class UpdateMeter(MeterInput):
    serial_number: str = Field(min_length=1, max_length=100)


class MeterDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    room_id: UUID
    kind: MeterKind
    serial_number: str
    initial_reading: Decimal
    created_at: datetime
    updated_at: datetime
    unit: str
    latest_reading_id: UUID | None = None
    latest_reading_value: Decimal | None = None
    latest_reading_period: date | None = None
    latest_reading_status: ReadingStatus | None = None


class MeterFilters(MeterInput):
    room_id: UUID | None = None
    kind: MeterKind | None = None
    period: date | None = None
    status: ReadingStatus | None = None
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class MeterPage(BaseModel):
    items: list[MeterDTO]
    total: int
    limit: int
    offset: int


class SubmitReading(MeterInput):
    period: date
    value: ReadingValue


class CreateReading(MeterInput):
    meter_id: UUID
    submitted_by: UUID
    period: date
    value: ReadingValue
    previous_value: ReadingValue


class ReadingDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    meter_id: UUID
    submitted_by: UUID
    period: date
    value: Decimal
    previous_value: Decimal
    consumption: Decimal
    status: ReadingStatus
    rejection_reason: str | None
    reviewed_by: UUID | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    correction_reason: str | None = None


class ReviewReading(MeterInput):
    status: ReadingStatus
    reason: str | None = Field(default=None, max_length=1000)


class CorrectReading(MeterInput):
    value: ReadingValue
    reason: str = Field(min_length=1, max_length=1000)


class ReadingFilters(MeterInput):
    status: ReadingStatus | None = None
    period: date | None = None
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ReadingPage(BaseModel):
    items: list[ReadingDTO]
    total: int
    limit: int
    offset: int
