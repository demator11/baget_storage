from datetime import date
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, File, Form, Query, Response, UploadFile, status

from baget_storage.api.auth import CurrentActor
from baget_storage.domain.entities.office import DocumentKind, DocumentStatus
from baget_storage.domain.use_cases.office import (
    AttachInvoiceDocumentUseCase,
    CreateInvoiceUseCase,
    CreateNotificationUseCase,
    CreateTicketUseCase,
    DownloadDocumentUseCase,
    DownloadInvoiceUseCase,
    GetDashboardUseCase,
    GetDocumentsUseCase,
    GetInvoicesUseCase,
    GetNotificationsUseCase,
    GetPaymentsUseCase,
    GetTicketHistoryUseCase,
    GetTicketsUseCase,
    MarkNotificationsReadUseCase,
    RecordPaymentUseCase,
    UpdateTicketUseCase,
    UploadDocumentUseCase,
)
from baget_storage.schemas.api.office import (
    AttachInvoiceDocumentRequest,
    CreateInvoiceRequest,
    CreateNotificationRequest,
    CreateTicketRequest,
    DashboardResponse,
    DocumentQuery,
    DocumentResponse,
    DocumentTitle,
    InvoiceQuery,
    InvoiceResponse,
    ListQuery,
    MarkReadResponse,
    NotificationQuery,
    NotificationResponse,
    PageResponse,
    PaymentResponse,
    RecordPaymentRequest,
    TicketEventResponse,
    TicketQuery,
    TicketResponse,
    UpdateTicketRequest,
)
from baget_storage.schemas.office import (
    AttachInvoiceDocument,
    CreateDocument,
    CreateInvoice,
    CreateNotification,
    CreateTicket,
    DocumentDownload,
    DocumentFilter,
    InvoiceFilter,
    ListFilter,
    NotificationFilter,
    RecordPayment,
    TicketFilter,
    UpdateTicket,
)

router = APIRouter(route_class=DishkaRoute)


def pdf_response(document: DocumentDownload) -> Response:
    filename = quote(document.filename, safe="")
    return Response(
        content=document.content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.post(
    "/tickets", response_model=TicketResponse, status_code=status.HTTP_201_CREATED, tags=["Tickets"]
)
async def create_ticket(
    request: CreateTicketRequest, actor: CurrentActor, use_case: FromDishka[CreateTicketUseCase]
) -> TicketResponse:
    result = await use_case.execute(actor, CreateTicket(**request.model_dump()))
    return TicketResponse(**result.model_dump())


@router.get("/tickets", response_model=PageResponse[TicketResponse], tags=["Tickets"])
async def list_tickets(
    filters: Annotated[TicketQuery, Query()],
    actor: CurrentActor,
    use_case: FromDishka[GetTicketsUseCase],
) -> PageResponse[TicketResponse]:
    result = await use_case.execute(actor, TicketFilter(**filters.model_dump()))
    return PageResponse[TicketResponse](**result.model_dump())


@router.patch("/tickets/{ticket_id}", response_model=TicketResponse, tags=["Tickets"])
async def update_ticket(
    ticket_id: UUID,
    request: UpdateTicketRequest,
    actor: CurrentActor,
    use_case: FromDishka[UpdateTicketUseCase],
) -> TicketResponse:
    result = await use_case.execute(
        actor, ticket_id, UpdateTicket(**request.model_dump(exclude_unset=True))
    )
    return TicketResponse(**result.model_dump())


@router.get(
    "/tickets/{ticket_id}/history",
    response_model=PageResponse[TicketEventResponse],
    tags=["Tickets"],
)
async def ticket_history(
    ticket_id: UUID,
    filters: Annotated[ListQuery, Query()],
    actor: CurrentActor,
    use_case: FromDishka[GetTicketHistoryUseCase],
) -> PageResponse[TicketEventResponse]:
    result = await use_case.execute(actor, ticket_id, ListFilter(**filters.model_dump()))
    return PageResponse[TicketEventResponse](**result.model_dump())


@router.post(
    "/invoices",
    response_model=InvoiceResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Billing"],
)
async def create_invoice(
    request: CreateInvoiceRequest, actor: CurrentActor, use_case: FromDishka[CreateInvoiceUseCase]
) -> InvoiceResponse:
    result = await use_case.execute(actor, CreateInvoice(**request.model_dump()))
    return InvoiceResponse(**result.model_dump())


@router.get("/invoices", response_model=PageResponse[InvoiceResponse], tags=["Billing"])
async def list_invoices(
    filters: Annotated[InvoiceQuery, Query()],
    actor: CurrentActor,
    use_case: FromDishka[GetInvoicesUseCase],
) -> PageResponse[InvoiceResponse]:
    result = await use_case.execute(actor, InvoiceFilter(**filters.model_dump()))
    return PageResponse[InvoiceResponse](**result.model_dump())


@router.put("/invoices/{invoice_id}/document", response_model=InvoiceResponse, tags=["Billing"])
async def attach_invoice_document(
    invoice_id: UUID,
    request: AttachInvoiceDocumentRequest,
    actor: CurrentActor,
    use_case: FromDishka[AttachInvoiceDocumentUseCase],
) -> InvoiceResponse:
    result = await use_case.execute(
        actor, invoice_id, AttachInvoiceDocument(**request.model_dump())
    )
    return InvoiceResponse(**result.model_dump())


@router.get("/invoices/{invoice_id}/download", tags=["Billing"])
async def download_invoice(
    invoice_id: UUID, actor: CurrentActor, use_case: FromDishka[DownloadInvoiceUseCase]
) -> Response:
    return pdf_response(await use_case.execute(actor, invoice_id))


@router.post(
    "/invoices/{invoice_id}/payments",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Billing"],
)
async def record_payment(
    invoice_id: UUID,
    request: RecordPaymentRequest,
    actor: CurrentActor,
    use_case: FromDishka[RecordPaymentUseCase],
) -> PaymentResponse:
    result = await use_case.execute(actor, invoice_id, RecordPayment(**request.model_dump()))
    return PaymentResponse(**result.model_dump())


@router.get("/payments", response_model=PageResponse[PaymentResponse], tags=["Billing"])
async def list_payments(
    filters: Annotated[ListQuery, Query()],
    actor: CurrentActor,
    use_case: FromDishka[GetPaymentsUseCase],
) -> PageResponse[PaymentResponse]:
    result = await use_case.execute(actor, ListFilter(**filters.model_dump()))
    return PageResponse[PaymentResponse](**result.model_dump())


@router.post(
    "/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Documents"],
)
async def upload_document(
    actor: CurrentActor,
    use_case: FromDishka[UploadDocumentUseCase],
    lease_id: Annotated[UUID, Form()],
    title: Annotated[DocumentTitle, Form()],
    kind: Annotated[DocumentKind, Form()],
    issued_on: Annotated[date, Form()],
    file: Annotated[UploadFile, File()],
    document_status: Annotated[DocumentStatus, Form(alias="status")] = DocumentStatus.AVAILABLE,
) -> DocumentResponse:
    content = await file.read(10 * 1024 * 1024 + 1)
    result = await use_case.execute(
        actor,
        CreateDocument(
            lease_id=lease_id, title=title, kind=kind, issued_on=issued_on, status=document_status
        ),
        file.filename or "document.pdf",
        content,
    )
    return DocumentResponse(**result.model_dump())


@router.get("/documents", response_model=PageResponse[DocumentResponse], tags=["Documents"])
async def list_documents(
    filters: Annotated[DocumentQuery, Query()],
    actor: CurrentActor,
    use_case: FromDishka[GetDocumentsUseCase],
) -> PageResponse[DocumentResponse]:
    result = await use_case.execute(actor, DocumentFilter(**filters.model_dump()))
    return PageResponse[DocumentResponse](**result.model_dump())


@router.get("/documents/{document_id}/download", tags=["Documents"])
async def download_document(
    document_id: UUID, actor: CurrentActor, use_case: FromDishka[DownloadDocumentUseCase]
) -> Response:
    return pdf_response(await use_case.execute(actor, document_id))


@router.get(
    "/notifications", response_model=PageResponse[NotificationResponse], tags=["Notifications"]
)
async def list_notifications(
    filters: Annotated[NotificationQuery, Query()],
    actor: CurrentActor,
    use_case: FromDishka[GetNotificationsUseCase],
) -> PageResponse[NotificationResponse]:
    result = await use_case.execute(actor, NotificationFilter(**filters.model_dump()))
    return PageResponse[NotificationResponse](**result.model_dump())


@router.post("/notifications/read-all", response_model=MarkReadResponse, tags=["Notifications"])
async def mark_all_notifications_read(
    actor: CurrentActor, use_case: FromDishka[MarkNotificationsReadUseCase]
) -> MarkReadResponse:
    return MarkReadResponse(count=await use_case.execute(actor))


@router.post(
    "/notifications/{notification_id}/read", response_model=MarkReadResponse, tags=["Notifications"]
)
async def mark_notification_read(
    notification_id: UUID, actor: CurrentActor, use_case: FromDishka[MarkNotificationsReadUseCase]
) -> MarkReadResponse:
    return MarkReadResponse(count=await use_case.execute(actor, notification_id))


@router.post(
    "/notifications",
    response_model=NotificationResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Notifications"],
)
async def create_notification(
    request: CreateNotificationRequest,
    actor: CurrentActor,
    use_case: FromDishka[CreateNotificationUseCase],
) -> NotificationResponse:
    result = await use_case.execute(actor, CreateNotification(**request.model_dump()))
    return NotificationResponse(**result.model_dump())


@router.get("/dashboard", response_model=DashboardResponse, tags=["Dashboard"])
async def dashboard(
    actor: CurrentActor, use_case: FromDishka[GetDashboardUseCase]
) -> DashboardResponse:
    result = await use_case.execute(actor)
    return DashboardResponse(**result.model_dump())
