from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from baget_storage.domain.entities.office import DocumentKind, DocumentStatus
from baget_storage.domain.entities.user import Role
from baget_storage.infrastructure.postgres.models.office import TicketOrm
from baget_storage.infrastructure.postgres.repository.office import (
    SqlAlchemyBillingRepository,
    SqlAlchemyDashboardRepository,
    SqlAlchemyDocumentRepository,
    SqlAlchemyTicketRepository,
)
from baget_storage.infrastructure.postgres.repository.rental import (
    SqlAlchemyLeaseRepository,
    SqlAlchemyRoomRepository,
)
from baget_storage.infrastructure.postgres.repository.user import SqlAlchemyUserRepository
from baget_storage.schemas.office import (
    CreateInvoice,
    DocumentFilter,
    DocumentInsert,
    InvoiceFilter,
    ListFilter,
    PaymentFilter,
    PaymentInsert,
    TicketFilter,
    TicketInsert,
)
from baget_storage.schemas.rental import CreateLease, CreateRoom, ExpiringLeaseFilter
from baget_storage.schemas.user import StoreUser

pytestmark = pytest.mark.integration


@pytest.fixture
async def screen_data(session_factory: async_sessionmaker[AsyncSession]) -> SimpleNamespace:
    today = datetime.now(UTC).date()
    async with session_factory() as session:
        users = SqlAlchemyUserRepository(session)
        tenant = await users.create(
            StoreUser(
                email="screen-tenant@example.com",
                full_name="Tenant Name",
                company_name="Tenant LLC",
                password_hash="hash",
                role=Role.TENANT,
            )
        )
        other = await users.create(
            StoreUser(
                email="screen-other@example.com",
                full_name="Other Tenant",
                password_hash="hash",
                role=Role.TENANT,
            )
        )
        manager = await users.create(
            StoreUser(
                email="screen-manager@example.com",
                full_name="Manager Name",
                password_hash="hash",
                role=Role.MANAGER,
            )
        )
        rooms = SqlAlchemyRoomRepository(session)
        stored_rooms = []
        for index in range(1, 5):
            stored_rooms.append(
                await rooms.create(
                    CreateRoom(
                        name=f"A-{index}",
                        building="A",
                        floor=1,
                        area_m2=Decimal(index * 100),
                        monthly_rent=Decimal("1000"),
                        status="maintenance" if index == 4 else "available",
                    )
                )
            )
        leases = SqlAlchemyLeaseRepository(session)
        own_lease = await leases.create(
            CreateLease(
                number="SCREEN-L1",
                room_id=stored_rooms[0].id,
                tenant_id=tenant.id,
                start_date=today - timedelta(days=1),
                end_date=today + timedelta(days=20),
                monthly_rent=Decimal("1000"),
            )
        )
        other_lease = await leases.create(
            CreateLease(
                number="SCREEN-L2",
                room_id=stored_rooms[1].id,
                tenant_id=other.id,
                start_date=today - timedelta(days=1),
                end_date=today + timedelta(days=40),
                monthly_rent=Decimal("2000"),
            )
        )
        billing = SqlAlchemyBillingRepository(session)
        invoice = await billing.create_invoice(
            CreateInvoice(
                lease_id=own_lease.id,
                number="SCREEN-I1",
                description="Rent",
                amount=Decimal("100"),
                issued_on=today - timedelta(days=1),
                due_date=today - timedelta(days=1),
            )
        )
        await billing.create_invoice(
            CreateInvoice(
                lease_id=own_lease.id,
                number="SCREEN-I2",
                description="Utilities",
                amount=Decimal("200"),
                issued_on=today,
                due_date=today + timedelta(days=1),
            )
        )
        await billing.create_invoice(
            CreateInvoice(
                lease_id=other_lease.id,
                number="SCREEN-I3",
                description="Other Rent",
                amount=Decimal("900"),
                issued_on=today,
                due_date=today,
            )
        )
        payment = await billing.record_payment(
            PaymentInsert(
                invoice_id=invoice.id,
                reference="SCREEN-P1",
                amount=Decimal("40"),
                paid_on=today,
            )
        )
        documents = SqlAlchemyDocumentRepository(session)
        document = await documents.create(
            DocumentInsert(
                lease_id=own_lease.id,
                title="Contract",
                kind=DocumentKind.CONTRACT,
                status=DocumentStatus.ACTIVE,
                issued_on=today,
                filename="contract.pdf",
                content=b"%PDF-1.7\ncontract",
            )
        )
        await documents.create(
            DocumentInsert(
                lease_id=other_lease.id,
                title="Other invoice",
                kind=DocumentKind.INVOICE,
                status=DocumentStatus.SIGNED,
                issued_on=today,
                filename="invoice.pdf",
                content=b"%PDF-1.7\ninvoice",
            )
        )
        tickets = SqlAlchemyTicketRepository(session)
        ticket = await tickets.create(
            TicketInsert(
                room_id=stored_rooms[0].id,
                created_by=tenant.id,
                title="Gate",
                description="Repair gate",
            )
        )
        await tickets.add_event(ticket.id, manager.id, "Assigned for repair")
        await tickets.create(
            TicketInsert(
                room_id=stored_rooms[1].id,
                created_by=other.id,
                title="Other gate",
                description="Repair other gate",
            )
        )
        await session.commit()
    return SimpleNamespace(
        tenant=tenant,
        other=other,
        manager=manager,
        room=stored_rooms[0],
        lease=own_lease,
        invoice=invoice,
        payment=payment,
        document=document,
        ticket=ticket,
        today=today,
    )


async def test_joined_screen_rows_include_names_and_scope_children(
    session_factory: async_sessionmaker[AsyncSession], screen_data: SimpleNamespace
) -> None:
    data = screen_data
    async with session_factory() as session:
        room = await SqlAlchemyRoomRepository(session).get_by_id(data.room.id)
        assert room is not None and room.active_lease is not None
        assert room.active_lease.tenant.company_name == "Tenant LLC"
        lease = await SqlAlchemyLeaseRepository(session).get_by_id(data.lease.id)
        assert lease is not None and lease.room is not None and lease.tenant is not None
        assert lease.room.name == "A-1" and lease.tenant.full_name == "Tenant Name"
        invoices = await SqlAlchemyBillingRepository(session).list_invoices(
            InvoiceFilter(), data.tenant.id
        )
        assert invoices.total == 2
        assert all(
            row.room.name == "A-1" and row.tenant.id == data.tenant.id for row in invoices.items
        )
        payments = await SqlAlchemyBillingRepository(session).list_payments(
            PaymentFilter(), data.tenant.id
        )
        assert payments.total == 1
        assert payments.items[0].invoice.number == "SCREEN-I1"
        assert payments.items[0].lease.number == "SCREEN-L1"
        documents = await SqlAlchemyDocumentRepository(session).list(
            DocumentFilter(), data.tenant.id
        )
        assert documents.total == 1 and documents.items[0].tenant.id == data.tenant.id
        assert documents.items[0].room.id == data.room.id
        tickets = await SqlAlchemyTicketRepository(session).list(TicketFilter(), data.tenant.id)
        assert tickets.total == 1
        assert tickets.items[0].number.startswith("REQ-")
        assert tickets.items[0].creator.full_name == "Tenant Name"
        assert tickets.items[0].room.name == "A-1"
        history = await SqlAlchemyTicketRepository(session).history(data.ticket.id, ListFilter())
        assert history.items[0].actor.full_name == "Manager Name"


async def test_screen_cards_and_nearest_payment_use_full_tenant_scope(
    session_factory: async_sessionmaker[AsyncSession], screen_data: SimpleNamespace
) -> None:
    data = screen_data
    async with session_factory() as session:
        rooms = SqlAlchemyRoomRepository(session)
        global_stats = await rooms.stats(None)
        own_stats = await rooms.stats(data.tenant.id)
        assert (
            global_stats.total,
            global_stats.occupied,
            global_stats.available,
            global_stats.maintenance,
        ) == (4, 2, 1, 1)
        assert global_stats.total_area_m2 == Decimal("1000")
        assert global_stats.available_area_m2 == Decimal("300")
        assert own_stats.total == own_stats.occupied == 1
        assert own_stats.total_area_m2 == Decimal("100")
        tickets = await SqlAlchemyTicketRepository(session).stats(data.tenant.id)
        assert tickets.total == tickets.new == 1
        documents = await SqlAlchemyDocumentRepository(session).stats(data.tenant.id)
        assert documents.total == documents.contracts == documents.active == 1
        assert documents.invoices == documents.signed == 0
        billing = await SqlAlchemyBillingRepository(session).summary(data.tenant.id)
        assert billing.due_count == 2 and billing.overdue_count == 1
        assert billing.unpaid_amount == Decimal("260")
        assert billing.paid_current_month == Decimal("40")
        assert billing.next_unpaid_invoice.id == data.invoice.id
        assert billing.last_payment.id == data.payment.id
        dashboard = await SqlAlchemyDashboardRepository(session).get(data.tenant.id, data.tenant.id)
        assert dashboard.next_payment.id == data.invoice.id
        assert dashboard.room_metrics == own_stats
        expiring = await SqlAlchemyLeaseRepository(session).expiring(
            ExpiringLeaseFilter(within_days=30), None
        )
        assert [row.id for row in expiring.items] == [data.lease.id]


async def test_month_filters_and_joined_pagination_do_not_add_per_row_queries(
    session_factory: async_sessionmaker[AsyncSession], screen_data: SimpleNamespace
) -> None:
    data = screen_data
    async with session_factory() as session:
        billing = SqlAlchemyBillingRepository(session)
        payments = await billing.list_payments(
            PaymentFilter(period=data.today.replace(day=1)), data.tenant.id
        )
        assert payments.total == 1
        empty = await billing.list_payments(
            PaymentFilter(paid_to=data.today - timedelta(days=1)), data.tenant.id
        )
        assert empty.total == 0
        invoices = await billing.list_invoices(InvoiceFilter(due_from=data.today), data.tenant.id)
        assert [row.number for row in invoices.items] == ["SCREEN-I2"]
        statements = []

        def record_statement(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        engine = session.get_bind()
        event.listen(engine, "before_cursor_execute", record_statement)
        try:
            page = await billing.list_invoices(InvoiceFilter(), data.tenant.id)
            payload = page.model_dump(mode="json")
        finally:
            event.remove(engine, "before_cursor_execute", record_statement)
        assert len(payload["items"]) == 2
        assert len(statements) == 2


async def test_ticket_month_is_half_open_utc_and_matches_stats(
    session_factory: async_sessionmaker[AsyncSession], screen_data: SimpleNamespace
) -> None:
    data = screen_data
    async with session_factory() as session:
        repository = SqlAlchemyTicketRepository(session)
        await session.execute(
            update(TicketOrm).values(created_at=datetime(2026, 10, 1, tzinfo=UTC))
        )
        included = []
        boundaries = [
            datetime(2026, 10, 31, 23, 59, 59, 999999, tzinfo=UTC),
            datetime(2026, 11, 1, tzinfo=UTC),
            datetime(2026, 11, 30, 23, 59, 59, 999999, tzinfo=UTC),
            datetime(2026, 12, 1, tzinfo=UTC),
        ]
        for index, created_at in enumerate(boundaries):
            ticket = await repository.create(
                TicketInsert(
                    room_id=data.room.id,
                    created_by=data.tenant.id,
                    title=f"Boundary {index}",
                    description="Period boundary",
                )
            )
            await session.execute(
                update(TicketOrm).where(TicketOrm.id == ticket.id).values(created_at=created_at)
            )
            if index in (1, 2):
                included.append(ticket.id)
        await session.commit()
        period = boundaries[1].date()
        page = await repository.list(TicketFilter(period=period), data.tenant.id)
        cards = await repository.stats(data.tenant.id, period)
        assert page.total == cards.total == cards.new == 2
        assert cards.period == period
        assert {row.id for row in page.items} == set(included)
