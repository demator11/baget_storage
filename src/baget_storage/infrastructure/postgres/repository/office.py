from calendar import monthrange
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import DateTime, Select, and_, case, func, literal, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import ScalarSelect

from baget_storage.core.exceptions import EntityNotFound
from baget_storage.domain.entities.office import DocumentStatus
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
from baget_storage.infrastructure.postgres.repository.notification import (
    SqlAlchemyNotificationRepository as SqlAlchemyNotificationRepository,
)
from baget_storage.infrastructure.postgres.repository.rental import SqlAlchemyRoomRepository
from baget_storage.schemas.office import (
    BillingSummaryDTO,
    CreateInvoice,
    DashboardDTO,
    DocumentDownload,
    DocumentDTO,
    DocumentFilter,
    DocumentInsert,
    DocumentStatsDTO,
    InvoiceDTO,
    InvoiceFilter,
    ListFilter,
    Page,
    PaymentDTO,
    PaymentFilter,
    PaymentInsert,
    TicketDTO,
    TicketEventDTO,
    TicketFilter,
    TicketInsert,
    TicketStatsDTO,
    UpdateTicket,
)
from baget_storage.schemas.references import InvoiceRef, LeaseRef, RoomRef, TenantRef, UserRef


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


async def paginate_joined[DTO: BaseModel](
    session: AsyncSession, statement: Select, filters: ListFilter, to_dto: Callable[..., DTO]
) -> Page[DTO]:
    total = await session.scalar(
        select(func.count()).select_from(statement.order_by(None).subquery())
    )
    rows = await session.execute(statement.limit(filters.limit).offset(filters.offset))
    return Page(
        items=[to_dto(*row) for row in rows],
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


def invoice_dto(
    row: InvoiceOrm, paid_amount: Decimal, lease: LeaseOrm, room: RoomOrm, tenant: UserOrm
) -> InvoiceDTO:
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
        lease=LeaseRef.model_validate(lease),
        room=RoomRef.model_validate(room),
        tenant=TenantRef.model_validate(tenant),
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

    async def user_name(self, user_id: UUID) -> str | None:
        return await self._session.scalar(select(UserOrm.full_name).where(UserOrm.id == user_id))


class SqlAlchemyTicketRepository(
    BaseSqlAlchemyRepository[TicketOrm, TicketInsert, UpdateTicket, TicketDTO]
):
    model = TicketOrm
    read_dto = TicketDTO

    @staticmethod
    def _within_period(statement: Select, period: date | None) -> Select:
        if period is None:
            return statement
        start = datetime(period.year, period.month, 1, tzinfo=UTC)
        end = literal(start, type_=DateTime(timezone=True)) + timedelta(
            days=monthrange(period.year, period.month)[1]
        )
        return statement.where(TicketOrm.created_at >= start, TicketOrm.created_at < end)

    @staticmethod
    def _query() -> Select:
        return (
            select(TicketOrm, RoomOrm, UserOrm)
            .select_from(TicketOrm)
            .join(RoomOrm, RoomOrm.id == TicketOrm.room_id)
            .join(UserOrm, UserOrm.id == TicketOrm.created_by)
        )

    def _joined_dto(self, ticket: TicketOrm, room: RoomOrm, creator: UserOrm) -> TicketDTO:
        return self._to_dto(ticket).model_copy(
            update={
                "number": f"REQ-{ticket.public_number:06d}",
                "room": RoomRef.model_validate(room),
                "creator": UserRef.model_validate(creator),
                "tenant": TenantRef.model_validate(creator) if creator.role == "tenant" else None,
            }
        )

    async def get_by_id(self, entity_id: UUID) -> TicketDTO | None:
        row = (await self._session.execute(self._query().where(TicketOrm.id == entity_id))).first()
        return self._joined_dto(*row) if row is not None else None

    async def create(self, data: TicketInsert) -> TicketDTO:
        created = await super().create(data)
        return await self.get_by_id(created.id) or created

    async def update(self, entity_id: UUID, data: UpdateTicket) -> TicketDTO | None:
        updated = await super().update(entity_id, data)
        return await self.get_by_id(entity_id) if updated is not None else None

    async def lock(self, ticket_id: UUID) -> TicketDTO | None:
        row = (
            await self._session.execute(
                self._query().where(TicketOrm.id == ticket_id).with_for_update(of=TicketOrm)
            )
        ).first()
        return self._joined_dto(*row) if row is not None else None

    async def list(self, filters: TicketFilter, tenant_id: UUID | None) -> Page[TicketDTO]:
        statement = self._within_period(self._query(), filters.period)
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
        return await paginate_joined(
            self._session,
            statement.order_by(TicketOrm.created_at.desc(), TicketOrm.id),
            filters,
            self._joined_dto,
        )

    async def add_event(self, ticket_id: UUID, actor_id: UUID, description: str) -> None:
        self._session.add(
            TicketEventOrm(ticket_id=ticket_id, actor_id=actor_id, description=description)
        )
        await self._session.flush()

    async def history(self, ticket_id: UUID, filters: ListFilter) -> Page[TicketEventDTO]:
        statement = (
            select(TicketEventOrm, UserOrm)
            .select_from(TicketEventOrm)
            .join(UserOrm, UserOrm.id == TicketEventOrm.actor_id)
            .where(TicketEventOrm.ticket_id == ticket_id)
        )
        if filters.search:
            statement = statement.where(
                TicketEventOrm.description.icontains(filters.search, autoescape=True)
            )
        return await paginate_joined(
            self._session,
            statement.order_by(TicketEventOrm.created_at, TicketEventOrm.id),
            filters,
            self._history_dto,
        )

    @staticmethod
    def _history_dto(event: TicketEventOrm, actor: UserOrm) -> TicketEventDTO:
        return TicketEventDTO.model_validate(event).model_copy(
            update={"actor": UserRef.model_validate(actor)}
        )

    async def stats(self, tenant_id: UUID | None, period: date | None = None) -> TicketStatsDTO:
        statement = select(
            func.count().label("total"),
            func.count().filter(TicketOrm.status == "new").label("new"),
            func.count().filter(TicketOrm.status == "in_progress").label("in_progress"),
            func.count().filter(TicketOrm.status == "closed").label("closed"),
        ).select_from(TicketOrm)
        if tenant_id is not None:
            statement = statement.where(TicketOrm.created_by == tenant_id)
        statement = self._within_period(statement, period)
        return TicketStatsDTO(
            **(await self._session.execute(statement)).mappings().one(), period=period
        )


class SqlAlchemyBillingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _invoice_query(tenant_id: UUID | None = None) -> Select:
        statement = (
            select(InvoiceOrm, invoice_paid_amount(), LeaseOrm, RoomOrm, UserOrm)
            .select_from(InvoiceOrm)
            .join(LeaseOrm, LeaseOrm.id == InvoiceOrm.lease_id)
            .join(RoomOrm, RoomOrm.id == LeaseOrm.room_id)
            .join(UserOrm, UserOrm.id == LeaseOrm.tenant_id)
        )
        if tenant_id is not None:
            statement = statement.where(LeaseOrm.tenant_id == tenant_id)
        return statement

    @staticmethod
    def _payment_query(tenant_id: UUID | None = None) -> Select:
        statement = (
            select(PaymentOrm, InvoiceOrm, LeaseOrm, RoomOrm, UserOrm)
            .select_from(PaymentOrm)
            .join(InvoiceOrm, InvoiceOrm.id == PaymentOrm.invoice_id)
            .join(LeaseOrm, LeaseOrm.id == InvoiceOrm.lease_id)
            .join(RoomOrm, RoomOrm.id == LeaseOrm.room_id)
            .join(UserOrm, UserOrm.id == LeaseOrm.tenant_id)
        )
        if tenant_id is not None:
            statement = statement.where(LeaseOrm.tenant_id == tenant_id)
        return statement

    @staticmethod
    def _payment_dto(
        payment: PaymentOrm, invoice: InvoiceOrm, lease: LeaseOrm, room: RoomOrm, tenant: UserOrm
    ) -> PaymentDTO:
        return PaymentDTO.model_validate(payment).model_copy(
            update={
                "invoice": InvoiceRef.model_validate(invoice),
                "lease": LeaseRef.model_validate(lease),
                "room": RoomRef.model_validate(room),
                "tenant": TenantRef.model_validate(tenant),
            }
        )

    async def create_invoice(self, data: CreateInvoice) -> InvoiceDTO:
        row = InvoiceOrm(**data.model_dump())
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        result = await self.get_invoice(row.id)
        if result is None:
            raise EntityNotFound("Invoice not found")
        return result

    async def get_invoice(self, invoice_id: UUID, *, lock: bool = False) -> InvoiceDTO | None:
        if lock:
            locked = await self._session.scalar(
                select(InvoiceOrm.id).where(InvoiceOrm.id == invoice_id).with_for_update()
            )
            if locked is None:
                return None
        statement = (
            self._invoice_query()
            .where(InvoiceOrm.id == invoice_id)
            .execution_options(populate_existing=True)
        )
        row = (await self._session.execute(statement)).first()
        return invoice_dto(*row) if row is not None else None

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
        statement = self._invoice_query(tenant_id)
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
        for column, lower, upper in (
            (InvoiceOrm.issued_on, filters.issued_from, filters.issued_to),
            (InvoiceOrm.due_date, filters.due_from, filters.due_to),
        ):
            if lower is not None:
                statement = statement.where(column >= lower)
            if upper is not None:
                statement = statement.where(column <= upper)
        if filters.period is not None:
            end = filters.period.replace(
                day=monthrange(filters.period.year, filters.period.month)[1]
            )
            statement = statement.where(InvoiceOrm.issued_on.between(filters.period, end))
        total = await self._session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = await self._session.execute(
            statement.order_by(InvoiceOrm.issued_on.desc(), InvoiceOrm.id)
            .limit(filters.limit)
            .offset(filters.offset)
        )
        return Page(
            items=[invoice_dto(*row) for row in rows],
            total=total or 0,
            limit=filters.limit,
            offset=filters.offset,
        )

    async def get_payment(self, reference: str) -> PaymentDTO | None:
        row = (
            await self._session.execute(
                self._payment_query().where(PaymentOrm.reference == reference)
            )
        ).first()
        return self._payment_dto(*row) if row is not None else None

    async def record_payment(self, data: PaymentInsert) -> PaymentDTO:
        row = PaymentOrm(**data.model_dump())
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        result = await self.get_payment(row.reference)
        if result is None:
            raise EntityNotFound("Payment not found")
        return result

    async def list_payments(
        self, filters: PaymentFilter, tenant_id: UUID | None
    ) -> Page[PaymentDTO]:
        statement = self._payment_query(tenant_id)
        if filters.room_id is not None:
            statement = statement.where(LeaseOrm.room_id == filters.room_id)
        if filters.search:
            statement = statement.where(
                PaymentOrm.reference.icontains(filters.search, autoescape=True)
            )
        if filters.paid_from is not None:
            statement = statement.where(PaymentOrm.paid_on >= filters.paid_from)
        if filters.paid_to is not None:
            statement = statement.where(PaymentOrm.paid_on <= filters.paid_to)
        if filters.period is not None:
            end = filters.period.replace(
                day=monthrange(filters.period.year, filters.period.month)[1]
            )
            statement = statement.where(PaymentOrm.paid_on.between(filters.period, end))
        return await paginate_joined(
            self._session,
            statement.order_by(PaymentOrm.paid_on.desc(), PaymentOrm.id),
            filters,
            self._payment_dto,
        )

    async def summary(self, tenant_id: UUID | None) -> BillingSummaryDTO:
        today = datetime.now(UTC).date()
        outstanding = (
            self._invoice_query(tenant_id)
            .with_only_columns(
                InvoiceOrm.due_date, (InvoiceOrm.amount - invoice_paid_amount()).label("remaining")
            )
            .subquery()
        )
        amounts = (
            (
                await self._session.execute(
                    select(
                        func.count().filter(outstanding.c.remaining > 0).label("due_count"),
                        func.count()
                        .filter(and_(outstanding.c.remaining > 0, outstanding.c.due_date < today))
                        .label("overdue_count"),
                        func.coalesce(func.sum(func.greatest(outstanding.c.remaining, 0)), 0).label(
                            "unpaid_amount"
                        ),
                    ).select_from(outstanding)
                )
            )
            .mappings()
            .one()
        )
        nearest = (
            await self._session.execute(
                self._invoice_query(tenant_id)
                .where(InvoiceOrm.amount > invoice_paid_amount())
                .order_by(InvoiceOrm.due_date, InvoiceOrm.id)
                .limit(1)
            )
        ).first()
        month_end = today.replace(day=monthrange(today.year, today.month)[1])
        paid_month = await self._session.scalar(
            self._payment_query(tenant_id)
            .with_only_columns(func.coalesce(func.sum(PaymentOrm.amount), 0))
            .where(PaymentOrm.paid_on.between(today.replace(day=1), month_end))
        )
        latest = (
            await self._session.execute(
                self._payment_query(tenant_id)
                .order_by(PaymentOrm.paid_on.desc(), PaymentOrm.created_at.desc(), PaymentOrm.id)
                .limit(1)
            )
        ).first()
        return BillingSummaryDTO(
            **amounts,
            paid_current_month=paid_month or Decimal("0.00"),
            next_unpaid_invoice=invoice_dto(*nearest) if nearest is not None else None,
            last_payment=self._payment_dto(*latest) if latest is not None else None,
        )


class SqlAlchemyDocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _query(tenant_id: UUID | None = None) -> Select:
        statement = (
            select(DocumentOrm, LeaseOrm, RoomOrm, UserOrm)
            .select_from(DocumentOrm)
            .join(LeaseOrm, LeaseOrm.id == DocumentOrm.lease_id)
            .join(RoomOrm, RoomOrm.id == LeaseOrm.room_id)
            .join(UserOrm, UserOrm.id == LeaseOrm.tenant_id)
        )
        if tenant_id is not None:
            statement = statement.where(LeaseOrm.tenant_id == tenant_id)
        return statement

    @staticmethod
    def _joined_dto(
        document: DocumentOrm, lease: LeaseOrm, room: RoomOrm, tenant: UserOrm
    ) -> DocumentDTO:
        return DocumentDTO.model_validate(document).model_copy(
            update={
                "lease": LeaseRef.model_validate(lease),
                "room": RoomRef.model_validate(room),
                "tenant": TenantRef.model_validate(tenant),
            }
        )

    async def create(self, data: DocumentInsert) -> DocumentDTO:
        row = DocumentOrm(**data.model_dump(), size_bytes=len(data.content))
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        result = await self.get_by_id(row.id)
        if result is None:
            raise EntityNotFound("Document not found")
        return result

    async def get_by_id(self, document_id: UUID, *, lock: bool = False) -> DocumentDTO | None:
        statement = (
            self._query()
            .where(DocumentOrm.id == document_id)
            .execution_options(populate_existing=True)
        )
        if lock:
            statement = statement.with_for_update(of=DocumentOrm)
        row = (await self._session.execute(statement)).first()
        return self._joined_dto(*row) if row is not None else None

    async def update_status(self, document_id: UUID, status: DocumentStatus) -> DocumentDTO:
        await self._session.execute(
            update(DocumentOrm).where(DocumentOrm.id == document_id).values(status=status)
        )
        result = await self.get_by_id(document_id)
        if result is None:
            raise EntityNotFound("Document not found")
        return result

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
        statement = self._query(tenant_id)
        if filters.room_id is not None:
            statement = statement.where(LeaseOrm.room_id == filters.room_id)
        if filters.kind is not None:
            statement = statement.where(DocumentOrm.kind == filters.kind)
        if filters.status is not None:
            statement = statement.where(DocumentOrm.status == filters.status)
        if filters.search:
            statement = statement.where(
                or_(
                    DocumentOrm.title.icontains(filters.search, autoescape=True),
                    DocumentOrm.filename.icontains(filters.search, autoescape=True),
                )
            )
        return await paginate_joined(
            self._session,
            statement.order_by(DocumentOrm.issued_on.desc(), DocumentOrm.id),
            filters,
            self._joined_dto,
        )

    async def stats(self, tenant_id: UUID | None) -> DocumentStatsDTO:
        documents = (
            self._query(tenant_id)
            .with_only_columns(DocumentOrm.kind, DocumentOrm.status)
            .subquery()
        )
        statement = select(
            func.count().label("total"),
            func.count().filter(documents.c.kind == "contract").label("contracts"),
            func.count().filter(documents.c.kind == "invoice").label("invoices"),
            func.count().filter(documents.c.kind == "act").label("acts"),
            func.count().filter(documents.c.status == "available").label("available"),
            func.count().filter(documents.c.status == "active").label("active"),
            func.count().filter(documents.c.status == "signed").label("signed"),
        ).select_from(documents)
        return DocumentStatsDTO(**(await self._session.execute(statement)).mappings().one())


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
        room_metrics = await SqlAlchemyRoomRepository(self._session).stats(tenant_id)
        billing = await SqlAlchemyBillingRepository(self._session).summary(tenant_id)
        return DashboardDTO(
            rooms_count=rooms_count,
            area_m2=area,
            monthly_rent=rent,
            unpaid_amount=unpaid or Decimal("0.00"),
            next_lease_end=lease_end,
            unread_notifications=unread or 0,
            open_tickets=open_tickets or 0,
            next_payment=billing.next_unpaid_invoice,
            room_metrics=room_metrics,
        )
