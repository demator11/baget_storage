from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from baget_storage.domain.entities.office import (
    DocumentKind,
    DocumentStatus,
    NotificationCategory,
    TicketStatus,
)


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, str_strip_whitespace=True)


class Page[T](DTO):
    items: list[T]
    total: int
    limit: int
    offset: int


class ListFilter(DTO):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    search: str | None = Field(default=None, max_length=200)
    room_id: UUID | None = None


class TicketFilter(ListFilter):
    status: TicketStatus | None = None


class CreateTicket(DTO):
    room_id: UUID
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=5000)
    due_date: date | None = None


class TicketInsert(CreateTicket):
    created_by: UUID


class UpdateTicket(DTO):
    status: TicketStatus | None = None
    assignee: str | None = Field(default=None, min_length=1, max_length=200)
    due_date: date | None = None

    @model_validator(mode="after")
    def require_status_when_supplied(self) -> "UpdateTicket":
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status cannot be null")
        return self


class TicketDTO(TicketInsert):
    id: UUID
    status: TicketStatus
    assignee: str | None
    created_at: datetime
    updated_at: datetime


class TicketEventDTO(DTO):
    id: UUID
    ticket_id: UUID
    actor_id: UUID
    description: str
    created_at: datetime


class CreateInvoice(DTO):
    lease_id: UUID
    document_id: UUID | None = None
    number: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    issued_on: date
    due_date: date

    @model_validator(mode="after")
    def valid_dates(self) -> "CreateInvoice":
        if self.due_date < self.issued_on:
            raise ValueError("due_date cannot precede issued_on")
        return self


class InvoiceDTO(CreateInvoice):
    id: UUID
    paid_amount: Decimal
    status: Literal["pending", "overdue", "paid"]
    created_at: datetime
    updated_at: datetime


class InvoiceFilter(ListFilter):
    status: Literal["pending", "overdue", "paid"] | None = None


class AttachInvoiceDocument(DTO):
    document_id: UUID


class RecordPayment(DTO):
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    reference: str = Field(min_length=1, max_length=150)
    paid_on: date


class PaymentInsert(RecordPayment):
    invoice_id: UUID


class PaymentDTO(PaymentInsert):
    id: UUID
    created_at: datetime


class CreateDocument(DTO):
    lease_id: UUID
    title: str = Field(min_length=1, max_length=200)
    kind: DocumentKind
    status: DocumentStatus = DocumentStatus.AVAILABLE
    issued_on: date


class DocumentInsert(CreateDocument):
    filename: str = Field(min_length=1, max_length=200)
    content: bytes = Field(min_length=1, max_length=10 * 1024 * 1024)


class DocumentDTO(CreateDocument):
    id: UUID
    filename: str
    size_bytes: int
    created_at: datetime
    updated_at: datetime


class DocumentDownload(DTO):
    filename: str
    content: bytes


class DocumentFilter(ListFilter):
    kind: DocumentKind | None = None


class CreateNotification(DTO):
    user_id: UUID
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=2000)
    category: NotificationCategory = NotificationCategory.SYSTEM


class NotificationDTO(CreateNotification):
    id: UUID
    read_at: datetime | None
    created_at: datetime


class NotificationFilter(ListFilter):
    category: NotificationCategory | None = None
    unread_only: bool = False


class DashboardDTO(DTO):
    rooms_count: int
    area_m2: Decimal
    monthly_rent: Decimal
    unpaid_amount: Decimal
    next_lease_end: date | None
    unread_notifications: int
    open_tickets: int
