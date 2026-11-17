from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from baget_storage.app import create_app
from baget_storage.core.config import Settings
from baget_storage.core.exceptions import AccessDenied, BusinessRuleViolation, Conflict
from baget_storage.domain.entities.office import DocumentKind, DocumentStatus
from baget_storage.domain.entities.user import Actor, Role
from baget_storage.domain.use_cases.office import (
    AttachInvoiceDocumentUseCase,
    CreateInvoiceUseCase,
    DownloadInvoiceUseCase,
)
from baget_storage.infrastructure.postgres.models.office import DocumentOrm, InvoiceOrm, PaymentOrm
from baget_storage.infrastructure.postgres.models.rental import LeaseOrm, RoomOrm
from baget_storage.infrastructure.postgres.models.user import UserOrm
from baget_storage.infrastructure.postgres.office_uow import OfficeSqlAlchemyUnitOfWork
from baget_storage.infrastructure.security import Argon2PasswordHasher
from baget_storage.schemas.office import (
    AttachInvoiceDocument,
    CreateInvoice,
    DocumentDTO,
    DocumentInsert,
    InvoiceDTO,
)

pytestmark = pytest.mark.integration
PDF = b"%PDF-1.4 invoice download test"
PASSWORD = "invoice-document-test-password"


@dataclass
class InvoiceDocumentContext:
    manager: Actor
    tenant: Actor
    other: Actor
    lease_id: UUID
    document: DocumentDTO
    wrong_kind: DocumentDTO
    wrong_lease: DocumentDTO
    invoice: InvoiceDTO


def invoice_data(lease_id: UUID, number: str, document_id: UUID | None = None) -> CreateInvoice:
    return CreateInvoice(
        lease_id=lease_id,
        number=number,
        description="Rental invoice",
        amount="1000",
        issued_on=date(2026, 11, 1),
        due_date=date(2026, 11, 30),
        document_id=document_id,
    )


@pytest.fixture
async def invoice_documents(
    session_factory: async_sessionmaker[AsyncSession],
) -> InvoiceDocumentContext:
    manager = Actor(id=uuid4(), role=Role.MANAGER)
    tenant = Actor(id=uuid4(), role=Role.TENANT)
    other = Actor(id=uuid4(), role=Role.TENANT)
    room_ids = [uuid4(), uuid4()]
    lease_ids = [uuid4(), uuid4()]
    password_hash = await Argon2PasswordHasher().hash(PASSWORD)
    async with session_factory.begin() as session:
        for actor, email in (
            (manager, "invoice-manager@example.com"),
            (tenant, "invoice-tenant@example.com"),
            (other, "invoice-other@example.com"),
        ):
            session.add(
                UserOrm(
                    id=actor.id,
                    email=email,
                    full_name=email,
                    role=actor.role,
                    password_hash=password_hash,
                )
            )
        for index, room_id in enumerate(room_ids):
            session.add(
                RoomOrm(
                    id=room_id,
                    name=f"Invoice room {index}",
                    building="A",
                    floor=1,
                    area_m2=100,
                    monthly_rent=1000,
                )
            )
        await session.flush()
        for index, actor in enumerate((tenant, other)):
            session.add(
                LeaseOrm(
                    id=lease_ids[index],
                    room_id=room_ids[index],
                    tenant_id=actor.id,
                    number=f"INV-LEASE-{index}",
                    start_date=date(2026, 1, 1),
                    end_date=date(2027, 12, 31),
                    monthly_rent=1000,
                )
            )
    documents = []
    async with OfficeSqlAlchemyUnitOfWork(session_factory) as uow:
        for lease_id, kind in (
            (lease_ids[0], DocumentKind.INVOICE),
            (lease_ids[0], DocumentKind.CONTRACT),
            (lease_ids[1], DocumentKind.INVOICE),
        ):
            documents.append(
                await uow.documents.create(
                    DocumentInsert(
                        lease_id=lease_id,
                        title="Invoice PDF",
                        kind=kind,
                        status=DocumentStatus.AVAILABLE,
                        issued_on=date(2026, 11, 1),
                        filename="Счёт ноябрь.pdf",
                        content=PDF,
                    )
                )
            )
        await uow.commit()
    invoice = await CreateInvoiceUseCase(OfficeSqlAlchemyUnitOfWork(session_factory)).execute(
        manager, invoice_data(lease_ids[0], "INVOICE-1")
    )
    return InvoiceDocumentContext(
        manager, tenant, other, lease_ids[0], documents[0], documents[1], documents[2], invoice
    )


async def test_attach_pdf_preserves_invoice_and_payment_values(
    session_factory, invoice_documents
) -> None:
    context = invoice_documents
    async with session_factory.begin() as session:
        session.add(
            PaymentOrm(
                invoice_id=context.invoice.id,
                amount=25,
                reference="INVOICE-PAYMENT",
                paid_on=date(2026, 11, 10),
            )
        )
    attach = AttachInvoiceDocumentUseCase(OfficeSqlAlchemyUnitOfWork(session_factory))
    result = await attach.execute(
        context.manager,
        context.invoice.id,
        AttachInvoiceDocument(document_id=context.document.id),
    )
    assert result.document_id == context.document.id
    assert result.created_at == context.invoice.created_at
    assert result.amount == context.invoice.amount
    assert result.paid_amount == Decimal("25")
    repeated = await attach.execute(
        context.manager,
        context.invoice.id,
        AttachInvoiceDocument(document_id=context.document.id),
    )
    assert repeated.updated_at == result.updated_at
    download = await DownloadInvoiceUseCase(OfficeSqlAlchemyUnitOfWork(session_factory)).execute(
        context.tenant, context.invoice.id
    )
    assert download.content == PDF
    assert download.filename == context.document.filename


async def test_invoice_can_be_created_with_attachment_and_document_is_unique(
    session_factory, invoice_documents
) -> None:
    context = invoice_documents
    linked = await CreateInvoiceUseCase(OfficeSqlAlchemyUnitOfWork(session_factory)).execute(
        context.manager, invoice_data(context.lease_id, "INVOICE-2", context.document.id)
    )
    assert linked.document_id == context.document.id
    with pytest.raises(Conflict):
        await AttachInvoiceDocumentUseCase(OfficeSqlAlchemyUnitOfWork(session_factory)).execute(
            context.manager,
            context.invoice.id,
            AttachInvoiceDocument(document_id=context.document.id),
        )
    async with session_factory() as session:
        original = await session.get(InvoiceOrm, context.invoice.id)
        assert original.document_id is None
    with pytest.raises(IntegrityError):
        async with session_factory.begin() as session:
            document = await session.get(DocumentOrm, context.document.id)
            await session.delete(document)
    async with session_factory() as session:
        assert await session.get(DocumentOrm, context.document.id) is not None


async def test_invalid_attachment_is_rejected_and_invoice_remains_unchanged(
    session_factory, invoice_documents
) -> None:
    context = invoice_documents
    use_case = AttachInvoiceDocumentUseCase(OfficeSqlAlchemyUnitOfWork(session_factory))
    for document in (context.wrong_kind, context.wrong_lease):
        with pytest.raises(BusinessRuleViolation):
            await use_case.execute(
                context.manager,
                context.invoice.id,
                AttachInvoiceDocument(document_id=document.id),
            )
    with pytest.raises(AccessDenied):
        await use_case.execute(
            context.tenant,
            context.invoice.id,
            AttachInvoiceDocument(document_id=context.document.id),
        )
    async with session_factory() as session:
        invoice = await session.get(InvoiceOrm, context.invoice.id)
        assert invoice.document_id is None


async def test_invoice_pdf_api_checks_ownership_and_returns_safe_download(
    session_factory, test_database_url, invoice_documents
) -> None:
    context = invoice_documents
    secret = "invoice-document-api-test-secret-value"
    app = create_app(
        Settings(database_url=test_database_url, jwt_secret=secret, environment="test")
    )
    invoice_path = f"/api/v1/invoices/{context.invoice.id}"
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {}
            for actor, email in (
                (context.manager, "invoice-manager@example.com"),
                (context.tenant, "invoice-tenant@example.com"),
                (context.other, "invoice-other@example.com"),
            ):
                login = await client.post(
                    "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
                )
                assert login.status_code == 200, login.text
                headers[actor.id] = {"Authorization": f"Bearer {login.json()['access_token']}"}
            no_document = await client.get(
                f"{invoice_path}/download", headers=headers[context.tenant.id]
            )
            assert no_document.status_code == 404
            denied = await client.put(
                f"{invoice_path}/document",
                headers=headers[context.tenant.id],
                json={"document_id": str(context.document.id)},
            )
            assert denied.status_code == 403
            attached = await client.put(
                f"{invoice_path}/document",
                headers=headers[context.manager.id],
                json={"document_id": str(context.document.id)},
            )
            assert attached.status_code == 200, attached.text
            assert attached.json()["document_id"] == str(context.document.id)
            for actor in (context.manager, context.tenant):
                downloaded = await client.get(f"{invoice_path}/download", headers=headers[actor.id])
                assert downloaded.status_code == 200, downloaded.text
                assert downloaded.content == PDF
                assert downloaded.headers["content-type"] == "application/pdf"
                assert downloaded.headers["cache-control"] == "private, no-store"
                assert downloaded.headers["x-content-type-options"] == "nosniff"
                assert downloaded.headers["content-disposition"].startswith(
                    "attachment; filename*="
                )
            other = await client.get(f"{invoice_path}/download", headers=headers[context.other.id])
            assert other.status_code == 404
            anonymous = await client.get(f"{invoice_path}/download")
            assert anonymous.status_code == 401
