from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from baget_storage.domain.entities.meter import MeterKind, ReadingStatus

MeterValue = Annotated[Decimal, Field(ge=0, max_digits=16, decimal_places=3)]


class CreateMeterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    room_id: UUID
    kind: MeterKind
    serial_number: str = Field(min_length=1, max_length=100)
    initial_reading: MeterValue = Decimal("0")


class MeterQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    room_id: UUID | None = None
    kind: MeterKind | None = None
    period: date | None = None
    status: ReadingStatus | None = None
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class MeterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    room_id: UUID
    kind: MeterKind
    serial_number: str
    initial_reading: Decimal
    unit: str
    latest_reading_id: UUID | None
    latest_reading_value: Decimal | None
    latest_reading_period: date | None
    latest_reading_status: ReadingStatus | None
    created_at: datetime
    updated_at: datetime


class MeterPageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[MeterResponse]
    total: int
    limit: int
    offset: int


class SubmitReadingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: date
    value: MeterValue


class ReviewReadingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: ReadingStatus
    reason: str | None = Field(default=None, max_length=1000)


class CorrectReadingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    value: MeterValue
    reason: str = Field(min_length=1, max_length=1000)


class ReadingQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ReadingStatus | None = None
    period: date | None = None
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ReadingResponse(BaseModel):
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


class ReadingPageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[ReadingResponse]
    total: int
    limit: int
    offset: int
