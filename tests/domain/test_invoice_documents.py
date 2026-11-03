from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

import pytest

from baget_storage.core.exceptions import AccessDenied, BusinessRuleViolation, EntityNotFound
from baget_storage.domain.entities.office import (
    DocumentKind,
    DocumentStatus,
    NotificationPreferences,
)
from baget_storage.domain.entities.user import Actor, Role
from baget_storage.domain.use_cases.office import (
    AttachInvoiceDocumentUseCase,
    CreateInvoiceUseCase,
    DownloadInvoiceUseCase,
)
from baget_storage.schemas.office import (
    AttachInvoiceDocument,
    CreateInvoice,
    CreateNotification,
    DocumentDownload,
    DocumentDTO,
    InvoiceDTO,
    NotificationDTO,
)

NOW = datetime(2026, 11, 17, 12, tzinfo=UTC)
PDF = b"%PDF-1.4 test invoice"


class FakeInvoiceBilling:
    def __init__(self) -> None:
        self.items: dict[UUID, InvoiceDTO] = {}

    async def create_invoice(self, data: CreateInvoice) -> InvoiceDTO:
        invoice = InvoiceDTO(
            **data.model_dump(),
            id=uuid4(),
            paid_amount=Decimal("0"),
            status="pending",
            created_at=NOW,
            updated_at=NOW,
        )
        self.items[invoice.id] = invoice
        return invoice

    async def get_invoice(self, invoice_id: UUID, *, lock: bool = False) -> InvoiceDTO | None:
        return self.items.get(invoice_id)

    async def attach_document(self, invoice_id: UUID, document_id: UUID) -> InvoiceDTO:
        updated = self.items[invoice_id].model_copy(update={"document_id": document_id})
        self.items[invoice_id] = updated
        return updated


class FakeInvoiceDocuments:
    def __init__(self) -> None:
        self.items: dict[UUID, DocumentDTO] = {}

    async def get_by_id(self, document_id: UUID) -> DocumentDTO | None:
        return self.items.get(document_id)

    async def download(self, document_id: UUID) -> DocumentDownload:
        return DocumentDownload(filename=self.items[document_id].filename, content=PDF)


class FakeInvoiceAccess:
    def __init__(self) -> None:
        self.owners: dict[UUID, UUID] = {}

    async def lease_owner(self, lease_id: UUID) -> UUID | None:
        return self.owners.get(lease_id)


class FakeInvoiceNotifications:
    def __init__(self) -> None:
        self.items: list[NotificationDTO] = []

    async def preferences(self, user_id: UUID) -> NotificationPreferences:
        return NotificationPreferences()

    async def create(self, data: CreateNotification) -> NotificationDTO:
        result = NotificationDTO(**data.model_dump(), id=uuid4(), read_at=None, created_at=NOW)
        self.items.append(result)
        return result


class FakeInvoiceUnitOfWork:
    def __init__(self) -> None:
        self.billing = FakeInvoiceBilling()
        self.documents = FakeInvoiceDocuments()
        self.access = FakeInvoiceAccess()
        self.notifications = FakeInvoiceNotifications()
        self.committed = False
        self.fail_commit = False

    async def __aenter__(self) -> Self:
        self.snapshot = deepcopy((self.billing, self.notifications))
        self.committed = False
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self.committed or exc is not None:
            self.billing, self.notifications = self.snapshot

    async def commit(self) -> None:
        if self.fail_commit:
            raise RuntimeError("Commit failed")
        self.committed = True


@pytest.fixture
def invoice_document_context():
    uow = FakeInvoiceUnitOfWork()
    manager = Actor(id=uuid4(), role=Role.MANAGER)
    tenant = Actor(id=uuid4(), role=Role.TENANT)
    lease_id = uuid4()
    uow.access.owners[lease_id] = tenant.id
    document = DocumentDTO(
        id=uuid4(),
        lease_id=lease_id,
        title="November invoice",
        kind=DocumentKind.INVOICE,
        status=DocumentStatus.AVAILABLE,
        issued_on=date(2026, 11, 1),
        filename="invoice.pdf",
        size_bytes=len(PDF),
        created_at=NOW,
        updated_at=NOW,
    )
    uow.documents.items[document.id] = document
    command = CreateInvoice(
        lease_id=lease_id,
        number="INVOICE-1",
        description="November rent",
        amount="1000",
        issued_on=date(2026, 11, 1),
        due_date=date(2026, 11, 30),
    )
    return uow, manager, tenant, document, command


async def test_create_invoice_with_pdf_and_download_as_owner_or_manager(
    invoice_document_context,
) -> None:
    uow, manager, tenant, document, command = invoice_document_context
    invoice = await CreateInvoiceUseCase(uow).execute(
        manager, command.model_copy(update={"document_id": document.id})
    )
    assert invoice.document_id == document.id
    for actor in (manager, tenant):
        result = await DownloadInvoiceUseCase(uow).execute(actor, invoice.id)
        assert result.filename == "invoice.pdf"
        assert result.content == PDF


async def test_manager_can_attach_pdf_later_and_repeating_attachment_is_idempotent(
    invoice_document_context,
) -> None:
    uow, manager, _, document, command = invoice_document_context
    invoice = await CreateInvoiceUseCase(uow).execute(manager, command)
    use_case = AttachInvoiceDocumentUseCase(uow)
    attached = await use_case.execute(
        manager, invoice.id, AttachInvoiceDocument(document_id=document.id)
    )
    assert attached.document_id == document.id
    repeated = await use_case.execute(
        manager, invoice.id, AttachInvoiceDocument(document_id=document.id)
    )
    assert repeated == attached
    assert not uow.committed


@pytest.mark.parametrize("invalid", ["kind", "lease"])
async def test_create_invoice_rejects_wrong_document_without_persisting(
    invoice_document_context, invalid
) -> None:
    uow, manager, _, document, command = invoice_document_context
    changes = {"kind": DocumentKind.CONTRACT} if invalid == "kind" else {"lease_id": uuid4()}
    uow.documents.items[document.id] = document.model_copy(update=changes)
    with pytest.raises(BusinessRuleViolation):
        await CreateInvoiceUseCase(uow).execute(
            manager, command.model_copy(update={"document_id": document.id})
        )
    assert not uow.billing.items
    assert not uow.notifications.items


@pytest.mark.parametrize("invalid", ["kind", "lease"])
async def test_attach_rejects_wrong_document_without_changing_invoice(
    invoice_document_context, invalid
) -> None:
    uow, manager, _, document, command = invoice_document_context
    invoice = await CreateInvoiceUseCase(uow).execute(manager, command)
    changes = {"kind": DocumentKind.CONTRACT} if invalid == "kind" else {"lease_id": uuid4()}
    uow.documents.items[document.id] = document.model_copy(update=changes)
    with pytest.raises(BusinessRuleViolation):
        await AttachInvoiceDocumentUseCase(uow).execute(
            manager, invoice.id, AttachInvoiceDocument(document_id=document.id)
        )
    assert uow.billing.items[invoice.id].document_id is None


async def test_tenant_cannot_attach_pdf_and_other_tenant_cannot_download(
    invoice_document_context,
) -> None:
    uow, manager, tenant, document, command = invoice_document_context
    invoice = await CreateInvoiceUseCase(uow).execute(
        manager, command.model_copy(update={"document_id": document.id})
    )
    with pytest.raises(AccessDenied):
        await AttachInvoiceDocumentUseCase(uow).execute(
            tenant, invoice.id, AttachInvoiceDocument(document_id=document.id)
        )
    other = Actor(id=uuid4(), role=Role.TENANT)
    with pytest.raises(EntityNotFound):
        await DownloadInvoiceUseCase(uow).execute(other, invoice.id)


async def test_invoice_without_pdf_or_missing_invoice_is_not_found(
    invoice_document_context,
) -> None:
    uow, manager, tenant, document, command = invoice_document_context
    invoice = await CreateInvoiceUseCase(uow).execute(manager, command)
    with pytest.raises(EntityNotFound):
        await DownloadInvoiceUseCase(uow).execute(tenant, invoice.id)
    with pytest.raises(EntityNotFound):
        await DownloadInvoiceUseCase(uow).execute(tenant, uuid4())
    with pytest.raises(EntityNotFound):
        await AttachInvoiceDocumentUseCase(uow).execute(
            manager, uuid4(), AttachInvoiceDocument(document_id=document.id)
        )
    with pytest.raises(EntityNotFound):
        await AttachInvoiceDocumentUseCase(uow).execute(
            manager, invoice.id, AttachInvoiceDocument(document_id=uuid4())
        )


async def test_attachment_commit_failure_preserves_original_invoice(
    invoice_document_context,
) -> None:
    uow, manager, _, document, command = invoice_document_context
    invoice = await CreateInvoiceUseCase(uow).execute(manager, command)
    uow.fail_commit = True
    with pytest.raises(RuntimeError, match="Commit failed"):
        await AttachInvoiceDocumentUseCase(uow).execute(
            manager, invoice.id, AttachInvoiceDocument(document_id=document.id)
        )
    assert uow.billing.items[invoice.id].document_id is None
