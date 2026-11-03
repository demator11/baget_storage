from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import Select, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import ScalarSelect

from baget_storage.core.exceptions import EntityNotFound
from baget_storage.domain.entities.office import NotificationPreferences
from baget_storage.infrastructure.postgres.models.office import (
    DocumentOrm,
    InvoiceOrm,
    NotificationOrm,
    PaymentOrm,
    TicketEventOrm,
    TicketOrm,
)
from baget_storage.infrastructure.postgres.models.rental import LeaseOrm, RoomOrm
from baget_storage.infrastructure.postgres.models.user import UserOrm
from baget_storage.infrastructure.postgres.repository.base import BaseSqlAlchemyRepository
from baget_storage.schemas.office import (
    CreateInvoice,
    CreateNotification,
    DashboardDTO,
    DocumentDownload,
    DocumentDTO,
    DocumentFilter,
    DocumentInsert,
    InvoiceDTO,
    InvoiceFilter,
    ListFilter,
    NotificationDTO,
    NotificationFilter,
    Page,
    PaymentDTO,
    PaymentInsert,
    TicketDTO,
    TicketEventDTO,
    TicketFilter,
    TicketInsert,
    UpdateTicket,
)


async def paginate[Model, DTO: BaseModel](
    session: AsyncSession,
    statement: Select[tuple[Model]],
    filters: ListFilter,
    to_dto: Callable[[Model], DTO],
) -> Page[DTO]:
    total = await session.scalar(
        select(func.count()).select_from(statement.order_by(None).subquery())
    )
    rows = await session.scalars(statement.limit(filters.limit).offset(filters.offset))
    return Page(
        items=[to_dto(row) for row in rows],
        total=total or 0,
        limit=filters.limit,
        offset=filters.offset,
    )


def invoice_paid_amount() -> ScalarSelect[Decimal]:
    return (
        select(func.coalesce(func.sum(PaymentOrm.amount), 0))
        .where(PaymentOrm.invoice_id == InvoiceOrm.id)
        .correlate(InvoiceOrm)
        .scalar_subquery()
    )


def invoice_dto(row: InvoiceOrm, paid_amount: Decimal) -> InvoiceDTO:
    invoice_status = "pending"
    if paid_amount >= row.amount:
        invoice_status = "paid"
    elif row.due_date < datetime.now(UTC).date():
        invoice_status = "overdue"
    return InvoiceDTO(
        **CreateInvoice.model_validate(row).model_dump(),
        id=row.id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        paid_amount=paid_amount,
        status=invoice_status,
    )


class SqlAlchemyAccessRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def room_exists(self, room_id: UUID) -> bool:
        return (
            await self._session.scalar(select(RoomOrm.id).where(RoomOrm.id == room_id)) is not None
        )

    async def room_access(self, room_id: UUID, tenant_id: UUID) -> bool:
        today = datetime.now(UTC).date()
        return (
            await self._session.scalar(
                select(LeaseOrm.id)
                .where(
                    LeaseOrm.room_id == room_id,
                    LeaseOrm.tenant_id == tenant_id,
                    LeaseOrm.status == "active",
                    LeaseOrm.start_date <= today,
                    LeaseOrm.end_date >= today,
                )
                .limit(1)
            )
            is not None
        )

    async def lease_owner(self, lease_id: UUID) -> UUID | None:
        return await self._session.scalar(select(LeaseOrm.tenant_id).where(LeaseOrm.id == lease_id))

    async def user_exists(self, user_id: UUID) -> bool:
        return (
            await self._session.scalar(select(UserOrm.id).where(UserOrm.id == user_id)) is not None
        )


class SqlAlchemyTicketRepository(
    BaseSqlAlchemyRepository[TicketOrm, TicketInsert, UpdateTicket, TicketDTO]
):
    model = TicketOrm
    read_dto = TicketDTO

    async def lock(self, ticket_id: UUID) -> TicketDTO | None:
        row = await self._session.scalar(
            select(TicketOrm).where(TicketOrm.id == ticket_id).with_for_update()
        )
        return self._to_dto(row) if row is not None else None

    async def list(self, filters: TicketFilter, tenant_id: UUID | None) -> Page[TicketDTO]:
        statement = select(TicketOrm)
        if tenant_id is not None:
            statement = statement.where(TicketOrm.created_by == tenant_id)
        if filters.room_id is not None:
            statement = statement.where(TicketOrm.room_id == filters.room_id)
        if filters.status is not None:
            statement = statement.where(TicketOrm.status == filters.status)
        if filters.search:
            statement = statement.where(
                or_(
                    TicketOrm.title.icontains(filters.search, autoescape=True),
                    TicketOrm.description.icontains(filters.search, autoescape=True),
                )
            )
        return await paginate(
            self._session,
            statement.order_by(TicketOrm.created_at.desc(), TicketOrm.id),
            filters,
            self._to_dto,
        )

    async def add_event(self, ticket_id: UUID, actor_id: UUID, description: str) -> None:
        self._session.add(
            TicketEventOrm(ticket_id=ticket_id, actor_id=actor_id, description=description)
        )
        await self._session.flush()

    async def history(self, ticket_id: UUID, filters: ListFilter) -> Page[TicketEventDTO]:
        statement = select(TicketEventOrm).where(TicketEventOrm.ticket_id == ticket_id)
        if filters.search:
            statement = statement.where(
                TicketEventOrm.description.icontains(filters.search, autoescape=True)
            )
        return await paginate(
            self._session,
            statement.order_by(TicketEventOrm.created_at, TicketEventOrm.id),
            filters,
            TicketEventDTO.model_validate,
        )


class SqlAlchemyBillingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_invoice(self, data: CreateInvoice) -> InvoiceDTO:
        row = InvoiceOrm(**data.model_dump())
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return invoice_dto(row, Decimal("0.00"))

    async def get_invoice(self, invoice_id: UUID, *, lock: bool = False) -> InvoiceDTO | None:
        statement = (
            select(InvoiceOrm)
            .where(InvoiceOrm.id == invoice_id)
            .execution_options(populate_existing=True)
        )
        if lock:
            statement = statement.with_for_update()
        row = await self._session.scalar(statement)
        if row is None:
            return None
        paid = await self._session.scalar(
            select(func.coalesce(func.sum(PaymentOrm.amount), 0)).where(
                PaymentOrm.invoice_id == invoice_id
            )
        )
        return invoice_dto(row, paid or Decimal("0.00"))

    async def attach_document(self, invoice_id: UUID, document_id: UUID) -> InvoiceDTO:
        row = await self._session.get(InvoiceOrm, invoice_id)
        if row is None:
            raise EntityNotFound("Invoice not found")
        row.document_id = document_id
        await self._session.flush()
        await self._session.refresh(row)
        result = await self.get_invoice(invoice_id)
        if result is None:
            raise EntityNotFound("Invoice not found")
        return result

    async def list_invoices(
        self, filters: InvoiceFilter, tenant_id: UUID | None
    ) -> Page[InvoiceDTO]:
        paid = invoice_paid_amount()
        statement = select(InvoiceOrm, paid.label("paid_amount")).join(LeaseOrm)
        if tenant_id is not None:
            statement = statement.where(LeaseOrm.tenant_id == tenant_id)
        if filters.room_id is not None:
            statement = statement.where(LeaseOrm.room_id == filters.room_id)
        if filters.search:
            statement = statement.where(
                or_(
                    InvoiceOrm.number.icontains(filters.search, autoescape=True),
                    InvoiceOrm.description.icontains(filters.search, autoescape=True),
                )
            )
        if filters.status is not None:
            status = case(
                (paid >= InvoiceOrm.amount, "paid"),
                (InvoiceOrm.due_date < datetime.now(UTC).date(), "overdue"),
                else_="pending",
            )
            statement = statement.where(status == filters.status)
        total = await self._session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = await self._session.execute(
            statement.order_by(InvoiceOrm.issued_on.desc(), InvoiceOrm.id)
            .limit(filters.limit)
            .offset(filters.offset)
        )
        return Page(
            items=[invoice_dto(row, paid_amount) for row, paid_amount in rows],
            total=total or 0,
            limit=filters.limit,
            offset=filters.offset,
        )

    async def get_payment(self, reference: str) -> PaymentDTO | None:
        row = await self._session.scalar(
            select(PaymentOrm).where(PaymentOrm.reference == reference)
        )
        return PaymentDTO.model_validate(row) if row is not None else None

    async def record_payment(self, data: PaymentInsert) -> PaymentDTO:
        row = PaymentOrm(**data.model_dump())
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return PaymentDTO.model_validate(row)

    async def list_payments(self, filters: ListFilter, tenant_id: UUID | None) -> Page[PaymentDTO]:
        statement = select(PaymentOrm).join(InvoiceOrm).join(LeaseOrm)
        if tenant_id is not None:
            statement = statement.where(LeaseOrm.tenant_id == tenant_id)
        if filters.room_id is not None:
            statement = statement.where(LeaseOrm.room_id == filters.room_id)
        if filters.search:
            statement = statement.where(
                PaymentOrm.reference.icontains(filters.search, autoescape=True)
            )
        return await paginate(
            self._session,
            statement.order_by(PaymentOrm.paid_on.desc(), PaymentOrm.id),
            filters,
            PaymentDTO.model_validate,
        )


class SqlAlchemyDocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, data: DocumentInsert) -> DocumentDTO:
        row = DocumentOrm(**data.model_dump(), size_bytes=len(data.content))
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return DocumentDTO.model_validate(row)

    async def get_by_id(self, document_id: UUID) -> DocumentDTO | None:
        row = await self._session.get(DocumentOrm, document_id)
        return DocumentDTO.model_validate(row) if row is not None else None

    async def download(self, document_id: UUID) -> DocumentDownload:
        row = (
            await self._session.execute(
                select(DocumentOrm.filename, DocumentOrm.content).where(
                    DocumentOrm.id == document_id
                )
            )
        ).one_or_none()
        if row is None:
            raise EntityNotFound("Document not found")
        return DocumentDownload(filename=row.filename, content=row.content)

    async def list(self, filters: DocumentFilter, tenant_id: UUID | None) -> Page[DocumentDTO]:
        statement = select(DocumentOrm).join(LeaseOrm)
        if tenant_id is not None:
            statement = statement.where(LeaseOrm.tenant_id == tenant_id)
        if filters.room_id is not None:
            statement = statement.where(LeaseOrm.room_id == filters.room_id)
        if filters.kind is not None:
            statement = statement.where(DocumentOrm.kind == filters.kind)
        if filters.search:
            statement = statement.where(
                or_(
                    DocumentOrm.title.icontains(filters.search, autoescape=True),
                    DocumentOrm.filename.icontains(filters.search, autoescape=True),
                )
            )
        return await paginate(
            self._session,
            statement.order_by(DocumentOrm.issued_on.desc(), DocumentOrm.id),
            filters,
            DocumentDTO.model_validate,
        )


class SqlAlchemyNotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def preferences(self, user_id: UUID) -> NotificationPreferences:
        row = (
            await self._session.execute(
                select(
                    UserOrm.notify_payments, UserOrm.notify_meters, UserOrm.notify_documents
                ).where(UserOrm.id == user_id)
            )
        ).one_or_none()
        if row is None:
            raise EntityNotFound("User not found")
        return NotificationPreferences(
            notify_payments=row.notify_payments,
            notify_meters=row.notify_meters,
            notify_documents=row.notify_documents,
        )

    async def create(self, data: CreateNotification) -> NotificationDTO:
        row = NotificationOrm(**data.model_dump())
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return NotificationDTO.model_validate(row)

    async def list(self, user_id: UUID, filters: NotificationFilter) -> Page[NotificationDTO]:
        statement = select(NotificationOrm).where(NotificationOrm.user_id == user_id)
        if filters.category is not None:
            statement = statement.where(NotificationOrm.category == filters.category)
        if filters.unread_only:
            statement = statement.where(NotificationOrm.read_at.is_(None))
        if filters.search:
            statement = statement.where(
                or_(
                    NotificationOrm.title.icontains(filters.search, autoescape=True),
                    NotificationOrm.body.icontains(filters.search, autoescape=True),
                )
            )
        return await paginate(
            self._session,
            statement.order_by(NotificationOrm.created_at.desc(), NotificationOrm.id),
            filters,
            NotificationDTO.model_validate,
        )

    async def mark_read(self, user_id: UUID, notification_id: UUID | None) -> int:
        statement = update(NotificationOrm).where(NotificationOrm.user_id == user_id)
        if notification_id is not None:
            statement = statement.where(NotificationOrm.id == notification_id)
        else:
            statement = statement.where(NotificationOrm.read_at.is_(None))
        ids = await self._session.scalars(
            statement.values(read_at=func.coalesce(NotificationOrm.read_at, func.now())).returning(
                NotificationOrm.id
            )
        )
        return len(ids.all())


class SqlAlchemyDashboardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, tenant_id: UUID | None, user_id: UUID) -> DashboardDTO:
        today = datetime.now(UTC).date()
        leases = (
            select(
                func.count(RoomOrm.id),
                func.coalesce(func.sum(RoomOrm.area_m2), 0),
                func.coalesce(func.sum(LeaseOrm.monthly_rent), 0),
                func.min(LeaseOrm.end_date),
            )
            .select_from(LeaseOrm)
            .join(RoomOrm)
            .where(
                LeaseOrm.status == "active",
                LeaseOrm.start_date <= today,
                LeaseOrm.end_date >= today,
            )
        )
        invoices = select(InvoiceOrm.amount - invoice_paid_amount()).join(LeaseOrm)
        tickets = select(func.count()).select_from(TicketOrm).where(TicketOrm.status != "closed")
        if tenant_id is not None:
            leases = leases.where(LeaseOrm.tenant_id == tenant_id)
            invoices = invoices.where(LeaseOrm.tenant_id == tenant_id)
            tickets = tickets.where(TicketOrm.created_by == tenant_id)
        rooms_count, area, rent, lease_end = (await self._session.execute(leases)).one()
        unpaid = await self._session.scalar(
            select(func.coalesce(func.sum(invoices.subquery().c[0]), 0))
        )
        unread = await self._session.scalar(
            select(func.count())
            .select_from(NotificationOrm)
            .where(NotificationOrm.user_id == user_id, NotificationOrm.read_at.is_(None))
        )
        open_tickets = await self._session.scalar(tickets)
        return DashboardDTO(
            rooms_count=rooms_count,
            area_m2=area,
            monthly_rent=rent,
            unpaid_amount=unpaid or Decimal("0.00"),
            next_lease_end=lease_end,
            unread_notifications=unread or 0,
            open_tickets=open_tickets or 0,
        )
