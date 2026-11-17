from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from baget_storage.domain.entities.rental import LeaseStatus, RoomStatus
from baget_storage.schemas.references import LeaseRef, RoomRef, TenantRef

type Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
type Area = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2)]
type Name = Annotated[str, Field(min_length=1, max_length=160)]


class RentalDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, from_attributes=True)


class CreateRoom(RentalDTO):
    name: Name
    area_m2: Area
    building: Name
    floor: int = Field(ge=-10, le=200)
    monthly_rent: Money
    status: RoomStatus = RoomStatus.AVAILABLE
    ceiling_height_m: Area | None = None
    gate_type: Annotated[str, Field(max_length=160)] | None = None
    description: Annotated[str, Field(max_length=5000)] | None = None


class UpdateRoom(RentalDTO):
    name: Name | None = None
    area_m2: Area | None = None
    building: Name | None = None
    floor: Annotated[int, Field(ge=-10, le=200)] | None = None
    monthly_rent: Money | None = None
    status: RoomStatus | None = None
    ceiling_height_m: Area | None = None
    gate_type: Annotated[str, Field(max_length=160)] | None = None
    description: Annotated[str, Field(max_length=5000)] | None = None

    @model_validator(mode="after")
    def reject_null_required_fields(self) -> UpdateRoom:
        required = {"name", "area_m2", "building", "floor", "monthly_rent", "status"}
        if any(getattr(self, name) is None for name in required & self.model_fields_set):
            raise ValueError("Required room fields cannot be null")
        return self


class ActiveLeaseDTO(LeaseRef):
    tenant: TenantRef


class RoomDTO(CreateRoom):
    id: UUID
    created_at: datetime
    updated_at: datetime
    active_lease: ActiveLeaseDTO | None = None


class RoomFilter(RentalDTO):
    search: Annotated[str, Field(max_length=160)] | None = None
    status: RoomStatus | None = None
    building: Name | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class CreateLease(RentalDTO):
    number: Annotated[str, Field(min_length=1, max_length=80)]
    room_id: UUID
    tenant_id: UUID
    start_date: date
    end_date: date
    monthly_rent: Money


class LeaseDTO(CreateLease):
    id: UUID
    status: LeaseStatus
    created_at: datetime
    updated_at: datetime
    room: RoomRef | None = None
    tenant: TenantRef | None = None


class LeaseFilter(RentalDTO):
    room_id: UUID | None = None
    tenant_id: UUID | None = None
    status: LeaseStatus | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ExpiringLeaseFilter(RentalDTO):
    within_days: int = Field(default=30, ge=0, le=366)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class RoomStatsDTO(RentalDTO):
    total: int
    occupied: int
    available: int
    maintenance: int
    total_area_m2: Decimal
    occupied_area_m2: Decimal
    available_area_m2: Decimal
    maintenance_area_m2: Decimal


class RentalPage[T](RentalDTO):
    items: list[T]
    total: int
    limit: int
    offset: int
