from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from baget_storage.domain.entities.rental import LeaseStatus
from baget_storage.domain.entities.user import Role


class ReferenceDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserRef(ReferenceDTO):
    id: UUID
    full_name: str
    role: Role


class TenantRef(UserRef):
    company_name: str | None
    email: str
    phone: str | None


class RoomRef(ReferenceDTO):
    id: UUID
    name: str
    building: str
    floor: int
    area_m2: Decimal


class LeaseRef(ReferenceDTO):
    id: UUID
    number: str
    start_date: date
    end_date: date
    monthly_rent: Decimal
    status: LeaseStatus


class InvoiceRef(ReferenceDTO):
    id: UUID
    number: str
    description: str
    amount: Decimal
    due_date: date
