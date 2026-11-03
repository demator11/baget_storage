from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from baget_storage.domain.entities.office import NotificationCategory
from baget_storage.schemas.office import (
    CreateInvoice,
    CreateNotification,
    CreateTicket,
    DashboardDTO,
    DocumentDTO,
    DocumentFilter,
    InvoiceDTO,
    InvoiceFilter,
    ListFilter,
    NotificationDTO,
    PaymentDTO,
    RecordPayment,
    TicketDTO,
    TicketEventDTO,
    TicketFilter,
    UpdateTicket,
)

DocumentTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class CreateTicketRequest(CreateTicket):
    pass


class UpdateTicketRequest(UpdateTicket):
    pass


class TicketResponse(TicketDTO):
    pass


class TicketEventResponse(TicketEventDTO):
    pass


class TicketQuery(TicketFilter):
    pass


class ListQuery(ListFilter):
    pass


class CreateInvoiceRequest(CreateInvoice):
    pass


class InvoiceResponse(InvoiceDTO):
    pass


class InvoiceQuery(InvoiceFilter):
    pass


class AttachInvoiceDocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID


class RecordPaymentRequest(RecordPayment):
    pass


class PaymentResponse(PaymentDTO):
    pass


class DocumentResponse(DocumentDTO):
    pass


class DocumentQuery(DocumentFilter):
    pass


class CreateNotificationRequest(CreateNotification):
    pass


class NotificationResponse(NotificationDTO):
    pass


class NotificationQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    search: str | None = Field(default=None, max_length=200)
    category: NotificationCategory | None = None
    unread_only: bool = False


class DashboardResponse(DashboardDTO):
    pass


class PageResponse[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class MarkReadResponse(BaseModel):
    count: int
