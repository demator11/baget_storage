import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from baget_storage.core.exceptions import BusinessRuleViolation, Conflict
from baget_storage.domain.entities.office import DocumentKind, DocumentStatus
from baget_storage.domain.entities.user import Actor, Role
from baget_storage.domain.use_cases.office import RecordPaymentUseCase
from baget_storage.infrastructure.postgres.models.office import DocumentOrm
from baget_storage.infrastructure.postgres.models.rental import LeaseOrm, RoomOrm
from baget_storage.infrastructure.postgres.models.user import UserOrm
from baget_storage.infrastructure.postgres.office_uow import OfficeSqlAlchemyUnitOfWork
from baget_storage.schemas.office import (
    CreateInvoice,
    CreateNotification,
    DocumentInsert,
    NotificationFilter,
    PaymentFilter,
    RecordPayment,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def invoice_context(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[Actor, CreateInvoice]:
    today = datetime.now(UTC).date()
    async with session_factory() as session:
        manager = UserOrm(
            email="manager@example.com", full_name="Manager", password_hash="test", role="manager"
        )
        tenant = UserOrm(
            email="tenant@example.com", full_name="Tenant", password_hash="test", role="tenant"
        )
        room = RoomOrm(name="B1", building="B", floor=1, area_m2=100, monthly_rent=100)
        session.add_all([manager, tenant, room])
        await session.flush()
        lease = LeaseOrm(
            number="LEASE-001",
            room_id=room.id,
            tenant_id=tenant.id,
            start_date=today,
            end_date=today + timedelta(days=365),
            monthly_rent=100,
        )
        session.add(lease)
        await session.commit()
        return (
            Actor(id=manager.id, role=Role.MANAGER),
            CreateInvoice(
                lease_id=lease.id,
                number="INV-001",
                description="Rent",
                amount=Decimal("100"),
                issued_on=today,
                due_date=today + timedelta(days=7),
            ),
        )


async def test_invoice_and_notification_roll_back_together_on_constraint_failure(
    session_factory: async_sessionmaker[AsyncSession], invoice_context: tuple[Actor, CreateInvoice]
) -> None:
    _, command = invoice_context
    invoice_id = None
    with pytest.raises(Conflict):
        async with OfficeSqlAlchemyUnitOfWork(session_factory) as uow:
            invoice = await uow.billing.create_invoice(command)
            invoice_id = invoice.id
            await uow.notifications.create(
                CreateNotification(user_id=uuid4(), title="Missing recipient", body="Failure")
            )
            await uow.commit()
    assert invoice_id is not None
    async with OfficeSqlAlchemyUnitOfWork(session_factory) as uow:
        assert await uow.billing.get_invoice(invoice_id) is None


async def test_concurrent_payments_cannot_overpay_invoice(
    session_factory: async_sessionmaker[AsyncSession], invoice_context: tuple[Actor, CreateInvoice]
) -> None:
    actor, command = invoice_context
    async with OfficeSqlAlchemyUnitOfWork(session_factory) as uow:
        invoice = await uow.billing.create_invoice(command)
        await uow.commit()
    results = await asyncio.gather(
        *(
            RecordPaymentUseCase(OfficeSqlAlchemyUnitOfWork(session_factory)).execute(
                actor,
                invoice.id,
                RecordPayment(
                    amount=Decimal("80"), reference=reference, paid_on=datetime.now(UTC).date()
                ),
            )
            for reference in ("BANK-001", "BANK-002")
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, BusinessRuleViolation) for result in results) == 1
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    async with OfficeSqlAlchemyUnitOfWork(session_factory) as uow:
        updated = await uow.billing.get_invoice(invoice.id)
        assert updated is not None
        assert updated.paid_amount == Decimal("80")
        owner = await uow.access.lease_owner(command.lease_id)
        assert owner is not None
        assert (await uow.notifications.list(owner, NotificationFilter())).total == 1


async def test_concurrent_duplicate_payment_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession], invoice_context: tuple[Actor, CreateInvoice]
) -> None:
    actor, command = invoice_context
    async with OfficeSqlAlchemyUnitOfWork(session_factory) as uow:
        invoice = await uow.billing.create_invoice(command)
        await uow.commit()
    payment = RecordPayment(
        amount=Decimal("100"), reference="BANK-001", paid_on=datetime.now(UTC).date()
    )
    first, second = await asyncio.gather(
        *(
            RecordPaymentUseCase(OfficeSqlAlchemyUnitOfWork(session_factory)).execute(
                actor, invoice.id, payment
            )
            for _ in range(2)
        )
    )
    assert first.id == second.id
    async with OfficeSqlAlchemyUnitOfWork(session_factory) as uow:
        updated = await uow.billing.get_invoice(invoice.id)
        assert updated is not None and updated.status == "paid"
        assert (await uow.billing.list_payments(PaymentFilter(), None)).total == 1
        owner = await uow.access.lease_owner(command.lease_id)
        assert owner is not None
        assert (await uow.notifications.list(owner, NotificationFilter())).total == 1


async def test_document_metadata_does_not_load_content(
    session_factory: async_sessionmaker[AsyncSession], invoice_context: tuple[Actor, CreateInvoice]
) -> None:
    _, invoice = invoice_context
    content = b"%PDF-1.7\nExample\n%%EOF"
    async with OfficeSqlAlchemyUnitOfWork(session_factory) as uow:
        document = await uow.documents.create(
            DocumentInsert(
                lease_id=invoice.lease_id,
                title="Contract",
                kind=DocumentKind.CONTRACT,
                status=DocumentStatus.ACTIVE,
                issued_on=invoice.issued_on,
                filename="contract.pdf",
                content=content,
            )
        )
        await uow.commit()
    async with session_factory() as session:
        row = await session.get(DocumentOrm, document.id)
        assert row is not None
        assert "content" in inspect(row).unloaded
    async with OfficeSqlAlchemyUnitOfWork(session_factory) as uow:
        metadata = await uow.documents.get_by_id(document.id)
        assert metadata is not None and metadata.size_bytes == len(content)
        assert (await uow.documents.download(document.id)).content == content
