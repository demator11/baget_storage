from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from baget_storage.domain.entities.office import NotificationCategory
from baget_storage.schemas.office import (
    BillingSummaryDTO,
    CreateInvoice,
    CreateTicket,
    DashboardDTO,
    DocumentDTO,
    DocumentFilter,
    DocumentStatsDTO,
    InvoiceDTO,
    InvoiceFilter,
    ListFilter,
    NotificationContent,
    NotificationDTO,
    PaymentDTO,
    PaymentFilter,
    RecordPayment,
    TicketDTO,
    TicketEventDTO,
    TicketFilter,
    TicketStatsDTO,
    TicketStatsFilter,
    UpdateDocumentStatus,
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


class PaymentQuery(PaymentFilter):
    pass


class DocumentResponse(DocumentDTO):
    pass


class DocumentQuery(DocumentFilter):
    pass


class CreateNotificationRequest(NotificationContent):
    user_id: UUID


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


class TicketStatsResponse(TicketStatsDTO):
    pass


class TicketStatsQuery(TicketStatsFilter):
    pass


class UpdateDocumentStatusRequest(UpdateDocumentStatus):
    pass


class DocumentStatsResponse(DocumentStatsDTO):
    pass


class BillingSummaryResponse(BillingSummaryDTO):
    pass


class PageResponse[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class MarkReadResponse(BaseModel):
    count: int
