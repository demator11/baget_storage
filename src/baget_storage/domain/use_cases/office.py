from datetime import UTC, date, datetime
from pathlib import PurePosixPath
from uuid import UUID

from baget_storage.core.exceptions import (
    AccessDenied,
    BusinessRuleViolation,
    Conflict,
    EntityNotFound,
)
from baget_storage.domain.entities.office import (
    DocumentKind,
    NotificationCategory,
    NotificationResource,
    TicketStatus,
    validate_document_status,
)
from baget_storage.domain.entities.user import Actor, Role
from baget_storage.domain.ports.office import OfficeUnitOfWork
from baget_storage.domain.use_cases.notifications import notify_managers, notify_user
from baget_storage.schemas.office import (
    AttachInvoiceDocument,
    BillingSummaryDTO,
    CreateDocument,
    CreateInvoice,
    CreateNotification,
    CreateTicket,
    DashboardDTO,
    DocumentDownload,
    DocumentDTO,
    DocumentFilter,
    DocumentInsert,
    DocumentStatsDTO,
    InvoiceDTO,
    InvoiceFilter,
    ListFilter,
    NotificationDTO,
    NotificationFilter,
    Page,
    PaymentDTO,
    PaymentFilter,
    PaymentInsert,
    RecordPayment,
    TicketDTO,
    TicketEventDTO,
    TicketFilter,
    TicketInsert,
    TicketStatsDTO,
    UpdateDocumentStatus,
    UpdateTicket,
)


def require_manager(actor: Actor) -> None:
    if actor.role != Role.MANAGER:
        raise AccessDenied()


def tenant_scope(actor: Actor) -> UUID | None:
    return actor.id if actor.role == Role.TENANT else None


async def require_lease_access(uow: OfficeUnitOfWork, actor: Actor, lease_id: UUID) -> UUID:
    owner = await uow.access.lease_owner(lease_id)
    if owner is None or (actor.role == Role.TENANT and owner != actor.id):
        raise EntityNotFound("Lease not found")
    return owner


async def require_invoice_document(
    uow: OfficeUnitOfWork, document_id: UUID, lease_id: UUID
) -> DocumentDTO:
    document = await uow.documents.get_by_id(document_id)
    if document is None:
        raise EntityNotFound("Document not found")
    if document.kind != DocumentKind.INVOICE:
        raise BusinessRuleViolation("An invoice must reference an invoice document.")
    if document.lease_id != lease_id:
        raise BusinessRuleViolation("The invoice and its document must belong to the same lease.")
    return document


class OfficeUseCase:
    def __init__(self, uow: OfficeUnitOfWork) -> None:
        self._uow = uow


class CreateTicketUseCase(OfficeUseCase):
    async def execute(self, actor: Actor, data: CreateTicket) -> TicketDTO:
        async with self._uow as uow:
            if not await uow.access.room_exists(data.room_id):
                raise EntityNotFound("Room not found")
            if actor.role == Role.TENANT and not await uow.access.room_access(
                data.room_id, actor.id
            ):
                raise EntityNotFound("Room not found")
            ticket = await uow.tickets.create(
                TicketInsert(**data.model_dump(), created_by=actor.id)
            )
            await uow.tickets.add_event(ticket.id, actor.id, "Ticket created")
            await notify_managers(
                uow.notifications,
                title="Новая заявка",
                body=ticket.title,
                category=NotificationCategory.TICKETS,
                resource_type=NotificationResource.TICKET,
                resource_id=ticket.id,
                event_key=f"ticket-created:{ticket.id}",
            )
            await uow.commit()
            return ticket


class GetTicketsUseCase(OfficeUseCase):
    async def execute(self, actor: Actor, filters: TicketFilter) -> Page[TicketDTO]:
        async with self._uow as uow:
            return await uow.tickets.list(filters, tenant_scope(actor))


class GetTicketHistoryUseCase(OfficeUseCase):
    async def execute(
        self, actor: Actor, ticket_id: UUID, filters: ListFilter
    ) -> Page[TicketEventDTO]:
        async with self._uow as uow:
            ticket = await uow.tickets.get_by_id(ticket_id)
            if ticket is None or (actor.role == Role.TENANT and ticket.created_by != actor.id):
                raise EntityNotFound("Ticket not found")
            return await uow.tickets.history(ticket_id, filters)


class UpdateTicketUseCase(OfficeUseCase):
    async def execute(self, actor: Actor, ticket_id: UUID, data: UpdateTicket) -> TicketDTO:
        require_manager(actor)
        async with self._uow as uow:
            ticket = await uow.tickets.lock(ticket_id)
            if ticket is None:
                raise EntityNotFound("Ticket not found")
            changes = data.model_dump(exclude_unset=True)
            if not changes:
                return ticket
            if ticket.status == TicketStatus.CLOSED:
                raise Conflict("Closed tickets cannot be changed")
            updated = await uow.tickets.update(ticket_id, data)
            if updated is None:
                raise EntityNotFound("Ticket not found")
            description = f"Status: {updated.status}; assignee: {updated.assignee or 'unassigned'}"
            await uow.tickets.add_event(ticket_id, actor.id, description)
            await notify_user(
                uow.notifications,
                CreateNotification(
                    user_id=ticket.created_by,
                    title=ticket.title,
                    body=description,
                    category=NotificationCategory.TICKETS,
                    resource_type=NotificationResource.TICKET,
                    resource_id=ticket.id,
                ),
            )
            await uow.commit()
            return updated


class TakeTicketUseCase(OfficeUseCase):
    async def execute(self, actor: Actor, ticket_id: UUID) -> TicketDTO:
        require_manager(actor)
        async with self._uow as uow:
            ticket = await uow.tickets.lock(ticket_id)
            if ticket is None:
                raise EntityNotFound("Ticket not found")
            if ticket.status != TicketStatus.NEW:
                raise Conflict("Only new tickets can be taken into work")
            assignee = await uow.access.user_name(actor.id)
            if assignee is None:
                raise EntityNotFound("Manager not found")
            updated = await uow.tickets.update(
                ticket_id, UpdateTicket(status=TicketStatus.IN_PROGRESS, assignee=assignee)
            )
            if updated is None:
                raise EntityNotFound("Ticket not found")
            description = f"Status: {updated.status}; assignee: {assignee}"
            await uow.tickets.add_event(ticket_id, actor.id, description)
            await notify_user(
                uow.notifications,
                CreateNotification(
                    user_id=ticket.created_by,
                    title=ticket.title,
                    body=description,
                    category=NotificationCategory.TICKETS,
                    resource_type=NotificationResource.TICKET,
                    resource_id=ticket.id,
                ),
            )
            await uow.commit()
            return updated


class CreateInvoiceUseCase(OfficeUseCase):
    async def execute(self, actor: Actor, data: CreateInvoice) -> InvoiceDTO:
        require_manager(actor)
        async with self._uow as uow:
            owner = await require_lease_access(uow, actor, data.lease_id)
            if data.document_id is not None:
                await require_invoice_document(uow, data.document_id, data.lease_id)
            invoice = await uow.billing.create_invoice(data)
            await notify_user(
                uow.notifications,
                CreateNotification(
                    user_id=owner,
                    title=f"Счёт {invoice.number}",
                    body=f"К оплате {invoice.amount} RUB до {invoice.due_date}",
                    category=NotificationCategory.PAYMENTS,
                    resource_type=NotificationResource.INVOICE,
                    resource_id=invoice.id,
                ),
            )
            await uow.commit()
            return invoice


class GetInvoicesUseCase(OfficeUseCase):
    async def execute(self, actor: Actor, filters: InvoiceFilter) -> Page[InvoiceDTO]:
        async with self._uow as uow:
            return await uow.billing.list_invoices(filters, tenant_scope(actor))


class AttachInvoiceDocumentUseCase(OfficeUseCase):
    async def execute(
        self, actor: Actor, invoice_id: UUID, data: AttachInvoiceDocument
    ) -> InvoiceDTO:
        require_manager(actor)
        async with self._uow as uow:
            invoice = await uow.billing.get_invoice(invoice_id, lock=True)
            if invoice is None:
                raise EntityNotFound("Invoice not found")
            await require_invoice_document(uow, data.document_id, invoice.lease_id)
            if invoice.document_id == data.document_id:
                return invoice
            result = await uow.billing.attach_document(invoice_id, data.document_id)
            await uow.commit()
            return result


class DownloadInvoiceUseCase(OfficeUseCase):
    async def execute(self, actor: Actor, invoice_id: UUID) -> DocumentDownload:
        async with self._uow as uow:
            invoice = await uow.billing.get_invoice(invoice_id)
            if invoice is None:
                raise EntityNotFound("Invoice not found")
            await require_lease_access(uow, actor, invoice.lease_id)
            if invoice.document_id is None:
                raise EntityNotFound("No PDF is attached to this invoice.")
            document = await require_invoice_document(uow, invoice.document_id, invoice.lease_id)
            return await uow.documents.download(document.id)


class RecordPaymentUseCase(OfficeUseCase):
    async def execute(self, actor: Actor, invoice_id: UUID, data: RecordPayment) -> PaymentDTO:
        require_manager(actor)
        if data.paid_on > datetime.now(UTC).date():
            raise BusinessRuleViolation("Payment date cannot be in the future")
        async with self._uow as uow:
            invoice = await uow.billing.get_invoice(invoice_id, lock=True)
            if invoice is None:
                raise EntityNotFound("Invoice not found")
            previous = await uow.billing.get_payment(data.reference)
            if previous is not None:
                if (previous.invoice_id, previous.amount, previous.paid_on) != (
                    invoice_id,
                    data.amount,
                    data.paid_on,
                ):
                    raise Conflict("Payment reference already belongs to another payment")
                return previous
            if data.amount > invoice.amount - invoice.paid_amount:
                raise BusinessRuleViolation("Payment exceeds the remaining invoice amount")
            owner = await require_lease_access(uow, actor, invoice.lease_id)
            payment = await uow.billing.record_payment(
                PaymentInsert(**data.model_dump(), invoice_id=invoice_id)
            )
            await notify_user(
                uow.notifications,
                CreateNotification(
                    user_id=owner,
                    title="Оплата получена",
                    body=f"По счёту {invoice.number} получено {payment.amount} RUB",
                    category=NotificationCategory.PAYMENTS,
                    resource_type=NotificationResource.INVOICE,
                    resource_id=invoice.id,
                ),
            )
            await uow.commit()
            return payment


class GetPaymentsUseCase(OfficeUseCase):
    async def execute(self, actor: Actor, filters: PaymentFilter) -> Page[PaymentDTO]:
        async with self._uow as uow:
            return await uow.billing.list_payments(filters, tenant_scope(actor))


class UploadDocumentUseCase(OfficeUseCase):
    async def execute(
        self, actor: Actor, data: CreateDocument, filename: str, content: bytes
    ) -> DocumentDTO:
        require_manager(actor)
        validate_document_status(data.kind, data.status)
        if not content or len(content) > 10 * 1024 * 1024:
            raise BusinessRuleViolation("PDF size must be between 1 byte and 10 MiB")
        if not content.startswith(b"%PDF-"):
            raise BusinessRuleViolation("Only PDF documents are supported")
        filename = PurePosixPath(filename.replace("\\", "/")).name
        if not filename or len(filename) > 200 or any(ord(char) < 32 for char in filename):
            raise BusinessRuleViolation("Invalid filename")
        async with self._uow as uow:
            owner = await require_lease_access(uow, actor, data.lease_id)
            document = await uow.documents.create(
                DocumentInsert(**data.model_dump(), filename=filename, content=content)
            )
            await notify_user(
                uow.notifications,
                CreateNotification(
                    user_id=owner,
                    title="Новый документ",
                    body=document.title,
                    category=NotificationCategory.DOCUMENTS,
                    resource_type=NotificationResource.DOCUMENT,
                    resource_id=document.id,
                ),
            )
            await uow.commit()
            return document


class GetDocumentsUseCase(OfficeUseCase):
    async def execute(self, actor: Actor, filters: DocumentFilter) -> Page[DocumentDTO]:
        async with self._uow as uow:
            return await uow.documents.list(filters, tenant_scope(actor))


class DownloadDocumentUseCase(OfficeUseCase):
    async def execute(self, actor: Actor, document_id: UUID) -> DocumentDownload:
        async with self._uow as uow:
            document = await uow.documents.get_by_id(document_id)
            if document is None:
                raise EntityNotFound("Document not found")
            await require_lease_access(uow, actor, document.lease_id)
            return await uow.documents.download(document_id)


class GetNotificationsUseCase(OfficeUseCase):
    async def execute(self, actor: Actor, filters: NotificationFilter) -> Page[NotificationDTO]:
        async with self._uow as uow:
            return await uow.notifications.list(actor.id, filters)


class MarkNotificationsReadUseCase(OfficeUseCase):
    async def execute(self, actor: Actor, notification_id: UUID | None = None) -> int:
        async with self._uow as uow:
            count = await uow.notifications.mark_read(actor.id, notification_id)
            if notification_id is not None and count == 0:
                raise EntityNotFound("Notification not found")
            await uow.commit()
            return count


class CreateNotificationUseCase(OfficeUseCase):
    async def execute(self, actor: Actor, data: CreateNotification) -> NotificationDTO:
        require_manager(actor)
        async with self._uow as uow:
            if not await uow.access.user_exists(data.user_id):
                raise EntityNotFound("User not found")
            if data.resource_type is not None and data.resource_id is not None:
                allowed = await uow.notifications.can_access_resource(
                    data.user_id, data.resource_type, data.resource_id
                )
                if not allowed:
                    raise AccessDenied("The recipient cannot access this notification resource.")
            notification = await notify_user(uow.notifications, data)
            await uow.commit()
            return notification


class GetDashboardUseCase(OfficeUseCase):
    async def execute(self, actor: Actor) -> DashboardDTO:
        async with self._uow as uow:
            return await uow.dashboard.get(tenant_scope(actor), actor.id)


class GetTicketUseCase(OfficeUseCase):
    async def execute(self, actor: Actor, ticket_id: UUID) -> TicketDTO:
        async with self._uow as uow:
            ticket = await uow.tickets.get_by_id(ticket_id)
            if ticket is None or (actor.role == Role.TENANT and ticket.created_by != actor.id):
                raise EntityNotFound("Ticket not found")
            return ticket


class GetTicketStatsUseCase(OfficeUseCase):
    async def execute(self, actor: Actor, period: date | None = None) -> TicketStatsDTO:
        if period is not None and period.day != 1:
            raise BusinessRuleViolation("The ticket period must be the first day of a month.")
        async with self._uow as uow:
            return await uow.tickets.stats(tenant_scope(actor), period)


class GetDocumentStatsUseCase(OfficeUseCase):
    async def execute(self, actor: Actor) -> DocumentStatsDTO:
        async with self._uow as uow:
            return await uow.documents.stats(tenant_scope(actor))


class GetBillingSummaryUseCase(OfficeUseCase):
    async def execute(self, actor: Actor) -> BillingSummaryDTO:
        async with self._uow as uow:
            return await uow.billing.summary(tenant_scope(actor))


class GetDocumentUseCase(OfficeUseCase):
    async def execute(self, actor: Actor, document_id: UUID) -> DocumentDTO:
        async with self._uow as uow:
            document = await uow.documents.get_by_id(document_id)
            if document is None:
                raise EntityNotFound("Document not found")
            await require_lease_access(uow, actor, document.lease_id)
            return document


class GetInvoiceUseCase(OfficeUseCase):
    async def execute(self, actor: Actor, invoice_id: UUID) -> InvoiceDTO:
        async with self._uow as uow:
            invoice = await uow.billing.get_invoice(invoice_id)
            if invoice is None:
                raise EntityNotFound("Invoice not found")
            await require_lease_access(uow, actor, invoice.lease_id)
            return invoice


class UpdateDocumentStatusUseCase(OfficeUseCase):
    async def execute(
        self, actor: Actor, document_id: UUID, data: UpdateDocumentStatus
    ) -> DocumentDTO:
        require_manager(actor)
        async with self._uow as uow:
            document = await uow.documents.get_by_id(document_id, lock=True)
            if document is None:
                raise EntityNotFound("Document not found")
            validate_document_status(document.kind, data.status)
            if document.status == data.status:
                return document
            owner = await require_lease_access(uow, actor, document.lease_id)
            updated = await uow.documents.update_status(document_id, data.status)
            await notify_user(
                uow.notifications,
                CreateNotification(
                    user_id=owner,
                    title="Статус документа изменён",
                    body=f"{updated.title}: {updated.status}",
                    category=NotificationCategory.DOCUMENTS,
                    resource_type=NotificationResource.DOCUMENT,
                    resource_id=document_id,
                ),
            )
            await uow.commit()
            return updated
