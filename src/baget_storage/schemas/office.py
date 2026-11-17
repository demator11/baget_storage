from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from baget_storage.domain.entities.office import (
    DocumentKind,
    DocumentStatus,
    NotificationCategory,
    NotificationResource,
    TicketStatus,
)
from baget_storage.schemas.references import InvoiceRef, LeaseRef, RoomRef, TenantRef, UserRef
from baget_storage.schemas.rental import RoomStatsDTO


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
    period: date | None = None

    @model_validator(mode="after")
    def validate_period(self) -> "TicketFilter":
        if self.period is not None and self.period.day != 1:
            raise ValueError("period must be the first day of a month")
        return self


class TicketStatsFilter(DTO):
    period: date | None = None

    @model_validator(mode="after")
    def validate_period(self) -> "TicketStatsFilter":
        if self.period is not None and self.period.day != 1:
            raise ValueError("period must be the first day of a month")
        return self


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
    public_number: int | None = None
    number: str | None = None
    room: RoomRef | None = None
    creator: UserRef | None = None
    tenant: TenantRef | None = None


class TicketEventDTO(DTO):
    id: UUID
    ticket_id: UUID
    actor_id: UUID
    description: str
    created_at: datetime
    actor: UserRef | None = None


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
    lease: LeaseRef | None = None
    room: RoomRef | None = None
    tenant: TenantRef | None = None


class InvoiceFilter(ListFilter):
    status: Literal["pending", "overdue", "paid"] | None = None
    issued_from: date | None = None
    issued_to: date | None = None
    due_from: date | None = None
    due_to: date | None = None
    period: date | None = None

    @model_validator(mode="after")
    def validate_ranges(self) -> "InvoiceFilter":
        for start, end in ((self.issued_from, self.issued_to), (self.due_from, self.due_to)):
            if start is not None and end is not None and start > end:
                raise ValueError("The start date cannot be after the end date")
        if self.period is not None and self.period.day != 1:
            raise ValueError("period must be the first day of a month")
        return self


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
    invoice: InvoiceRef | None = None
    lease: LeaseRef | None = None
    room: RoomRef | None = None
    tenant: TenantRef | None = None


class PaymentFilter(ListFilter):
    paid_from: date | None = None
    paid_to: date | None = None
    period: date | None = None

    @model_validator(mode="after")
    def validate_ranges(self) -> "PaymentFilter":
        if (
            self.paid_from is not None
            and self.paid_to is not None
            and self.paid_from > self.paid_to
        ):
            raise ValueError("paid_from cannot be after paid_to")
        if self.period is not None and self.period.day != 1:
            raise ValueError("period must be the first day of a month")
        return self


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
    lease: LeaseRef | None = None
    room: RoomRef | None = None
    tenant: TenantRef | None = None


class DocumentDownload(DTO):
    filename: str
    content: bytes


class DocumentFilter(ListFilter):
    kind: DocumentKind | None = None
    status: DocumentStatus | None = None


class UpdateDocumentStatus(DTO):
    status: DocumentStatus


class TicketStatsDTO(DTO):
    total: int
    new: int
    in_progress: int
    closed: int
    period: date | None = None


class DocumentStatsDTO(DTO):
    total: int
    contracts: int
    invoices: int
    acts: int
    available: int
    active: int
    signed: int


class BillingSummaryDTO(DTO):
    next_unpaid_invoice: InvoiceDTO | None
    due_count: int
    overdue_count: int
    unpaid_amount: Decimal
    paid_current_month: Decimal
    last_payment: PaymentDTO | None


class NotificationContent(DTO):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=2000)
    category: NotificationCategory = NotificationCategory.SYSTEM
    resource_type: NotificationResource | None = None
    resource_id: UUID | None = None

    @model_validator(mode="after")
    def require_resource_pair(self) -> "NotificationContent":
        if (self.resource_type is None) != (self.resource_id is None):
            raise ValueError("resource_type and resource_id must be supplied together")
        return self


class CreateNotification(NotificationContent):
    user_id: UUID
    deduplication_key: str | None = Field(default=None, max_length=200, exclude=True)


class NotificationDTO(NotificationContent):
    user_id: UUID
    id: UUID
    read_at: datetime | None
    created_at: datetime


class NotificationCountsDTO(DTO):
    total: int
    unread: int
    categories: dict[NotificationCategory, int]
    unread_by_category: dict[NotificationCategory, int]


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
    next_payment: InvoiceDTO | None = None
    room_metrics: RoomStatsDTO | None = None
