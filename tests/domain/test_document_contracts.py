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
    NotificationResource,
    validate_document_status,
)
from baget_storage.domain.entities.user import Actor, Role
from baget_storage.domain.use_cases.office import (
    GetDocumentUseCase,
    GetInvoiceUseCase,
    UpdateDocumentStatusUseCase,
)
from baget_storage.schemas.api.archive import ArchiveQuery
from baget_storage.schemas.office import (
    CreateNotification,
    DocumentDTO,
    DocumentFilter,
    InvoiceDTO,
    NotificationDTO,
    UpdateDocumentStatus,
)


class FakeDocuments:
    def __init__(self, document: DocumentDTO) -> None:
        self.rows = {document.id: document}

    async def get_by_id(self, document_id: UUID, *, lock: bool = False) -> DocumentDTO | None:
        return self.rows.get(document_id)

    async def update_status(self, document_id: UUID, status: DocumentStatus) -> DocumentDTO:
        self.rows[document_id] = self.rows[document_id].model_copy(update={"status": status})
        return self.rows[document_id]


class FakeBilling:
    def __init__(self, invoice: InvoiceDTO) -> None:
        self.invoice = invoice

    async def get_invoice(self, invoice_id: UUID, *, lock: bool = False) -> InvoiceDTO | None:
        return self.invoice if self.invoice.id == invoice_id else None


class FakeAccess:
    def __init__(self, lease_id: UUID, tenant_id: UUID) -> None:
        self.lease_id = lease_id
        self.tenant_id = tenant_id

    async def lease_owner(self, lease_id: UUID) -> UUID | None:
        return self.tenant_id if lease_id == self.lease_id else None


class FakeNotifications:
    def __init__(self) -> None:
        self.items: list[NotificationDTO] = []
        self.fail = False

    async def create(self, data: CreateNotification) -> NotificationDTO:
        if self.fail:
            raise RuntimeError("Notification failed")
        result = NotificationDTO(
            **data.model_dump(), id=uuid4(), read_at=None, created_at=datetime.now(UTC)
        )
        self.items.append(result)
        return result

    async def preferences(self, user_id: UUID) -> NotificationPreferences:
        return NotificationPreferences()

    async def verified_email(self, user_id: UUID) -> str | None:
        return None


class FakeDocumentUow:
    def __init__(self, document: DocumentDTO, invoice: InvoiceDTO, owner_id: UUID) -> None:
        self.documents = FakeDocuments(document)
        self.billing = FakeBilling(invoice)
        self.access = FakeAccess(document.lease_id, owner_id)
        self.notifications = FakeNotifications()
        self.committed = False

    async def __aenter__(self) -> Self:
        self.snapshot = deepcopy((self.documents.rows, self.notifications.items))
        self.committed = False
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self.committed or exc is not None:
            self.documents.rows, self.notifications.items = self.snapshot

    async def commit(self) -> None:
        self.committed = True


@pytest.fixture
def context() -> tuple[FakeDocumentUow, Actor, Actor, DocumentDTO, InvoiceDTO]:
    now = datetime.now(UTC)
    tenant, manager = Actor(uuid4(), Role.TENANT), Actor(uuid4(), Role.MANAGER)
    lease_id = uuid4()
    document = DocumentDTO(
        id=uuid4(),
        lease_id=lease_id,
        title="Contract",
        kind=DocumentKind.CONTRACT,
        status=DocumentStatus.AVAILABLE,
        issued_on=date(2025, 1, 1),
        filename="contract.pdf",
        size_bytes=10,
        created_at=now,
        updated_at=now,
    )
    invoice = InvoiceDTO(
        id=uuid4(),
        lease_id=lease_id,
        number="I-1",
        description="Rent",
        amount=Decimal("100"),
        paid_amount=Decimal("0"),
        status="overdue",
        issued_on=date(2025, 1, 1),
        due_date=date(2025, 1, 10),
        created_at=now,
        updated_at=now,
    )
    return FakeDocumentUow(document, invoice, tenant.id), tenant, manager, document, invoice


async def test_historical_document_and_invoice_details_require_lease_ownership(context) -> None:
    uow, tenant, manager, document, invoice = context
    assert await GetDocumentUseCase(uow).execute(tenant, document.id) == document
    assert await GetInvoiceUseCase(uow).execute(tenant, invoice.id) == invoice
    assert await GetDocumentUseCase(uow).execute(manager, document.id) == document
    foreign = Actor(uuid4(), Role.TENANT)
    with pytest.raises(EntityNotFound):
        await GetDocumentUseCase(uow).execute(foreign, document.id)
    with pytest.raises(EntityNotFound):
        await GetInvoiceUseCase(uow).execute(foreign, invoice.id)
    with pytest.raises(EntityNotFound):
        await GetDocumentUseCase(uow).execute(tenant, uuid4())
    with pytest.raises(EntityNotFound):
        await GetInvoiceUseCase(uow).execute(tenant, uuid4())


async def test_manager_changes_document_status_and_notifies_owner_once(context) -> None:
    uow, tenant, manager, document, _ = context
    use_case = UpdateDocumentStatusUseCase(uow)
    result = await use_case.execute(manager, document.id, UpdateDocumentStatus(status="active"))
    assert result.status == DocumentStatus.ACTIVE
    assert uow.committed
    assert len(uow.notifications.items) == 1
    notification = uow.notifications.items[0]
    assert notification.user_id == tenant.id
    assert notification.resource_type == NotificationResource.DOCUMENT
    assert notification.resource_id == document.id
    repeated = await use_case.execute(manager, document.id, UpdateDocumentStatus(status="active"))
    assert repeated == result and len(uow.notifications.items) == 1


async def test_tenant_cannot_change_status_even_for_own_document(context) -> None:
    uow, tenant, _, document, _ = context
    with pytest.raises(AccessDenied):
        await UpdateDocumentStatusUseCase(uow).execute(
            tenant, document.id, UpdateDocumentStatus(status="signed")
        )
    assert uow.documents.rows[document.id].status == DocumentStatus.AVAILABLE
    assert not uow.notifications.items


async def test_status_change_rolls_back_when_notification_fails(context) -> None:
    uow, _, manager, document, _ = context
    uow.notifications.fail = True
    with pytest.raises(RuntimeError, match="Notification failed"):
        await UpdateDocumentStatusUseCase(uow).execute(
            manager, document.id, UpdateDocumentStatus(status="signed")
        )
    assert uow.documents.rows[document.id].status == DocumentStatus.AVAILABLE
    assert not uow.notifications.items


@pytest.mark.parametrize(
    "kind,status,allowed",
    [
        (DocumentKind.INVOICE, DocumentStatus.AVAILABLE, True),
        (DocumentKind.INVOICE, DocumentStatus.ACTIVE, False),
        (DocumentKind.INVOICE, DocumentStatus.SIGNED, False),
        (DocumentKind.ACT, DocumentStatus.AVAILABLE, True),
        (DocumentKind.ACT, DocumentStatus.ACTIVE, False),
        (DocumentKind.ACT, DocumentStatus.SIGNED, True),
        (DocumentKind.CONTRACT, DocumentStatus.AVAILABLE, True),
        (DocumentKind.CONTRACT, DocumentStatus.ACTIVE, True),
        (DocumentKind.CONTRACT, DocumentStatus.SIGNED, True),
    ],
)
def test_document_status_rules(kind, status, allowed) -> None:
    if allowed:
        validate_document_status(kind, status)
    else:
        with pytest.raises(BusinessRuleViolation):
            validate_document_status(kind, status)


def test_archive_preserves_selected_document_status() -> None:
    query = ArchiveQuery(status="signed", kind="act")
    filters = DocumentFilter(**query.model_dump())
    assert filters.status == DocumentStatus.SIGNED
    assert filters.kind == DocumentKind.ACT
