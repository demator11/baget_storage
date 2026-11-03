from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from baget_storage.core.exceptions import (
    AccessDenied,
    BusinessRuleViolation,
    Conflict,
    EntityNotFound,
)
from baget_storage.domain.entities.office import (
    DocumentKind,
    NotificationCategory,
    NotificationPreferences,
)
from baget_storage.domain.entities.user import Actor, Role
from baget_storage.domain.use_cases.office import (
    CreateNotificationUseCase,
    DownloadDocumentUseCase,
    RecordPaymentUseCase,
    UploadDocumentUseCase,
)
from baget_storage.schemas.office import (
    CreateDocument,
    CreateNotification,
    DocumentDTO,
    InvoiceDTO,
    NotificationDTO,
    PaymentDTO,
    RecordPayment,
)


class FakeBilling:
    def __init__(self, invoice):
        self.invoice = invoice
        self.payments = {}

    async def get_invoice(self, invoice_id, *, lock=False):
        return self.invoice if self.invoice.id == invoice_id else None

    async def get_payment(self, reference):
        return self.payments.get(reference)

    async def record_payment(self, data):
        result = PaymentDTO(**data.model_dump(), id=uuid4(), created_at=datetime.now(UTC))
        self.payments[data.reference] = result
        self.invoice = self.invoice.model_copy(
            update={"paid_amount": self.invoice.paid_amount + data.amount}
        )
        return result


class FakeAccess:
    def __init__(self, lease_id, tenant_id):
        self.lease_id = lease_id
        self.tenant_id = tenant_id

    async def lease_owner(self, lease_id):
        return self.tenant_id if lease_id == self.lease_id else None

    async def user_exists(self, user_id):
        return user_id == self.tenant_id


class FakeNotifications:
    def __init__(self):
        self.items = []
        self.fail = False
        self.settings = NotificationPreferences()

    async def preferences(self, user_id):
        return self.settings

    async def create(self, data):
        if self.fail:
            raise RuntimeError("Notification storage unavailable")
        result = NotificationDTO(
            **data.model_dump(), id=uuid4(), created_at=datetime.now(UTC), read_at=None
        )
        self.items.append(result)
        return result


class FakeDocuments:
    def __init__(self):
        self.items = {}

    async def create(self, data):
        result = DocumentDTO(
            **data.model_dump(exclude={"content"}),
            id=uuid4(),
            size_bytes=len(data.content),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.items[result.id] = result
        return result

    async def get_by_id(self, document_id):
        return self.items.get(document_id)


class FakeOfficeUow:
    def __init__(self, invoice, tenant_id):
        self.billing = FakeBilling(invoice)
        self.access = FakeAccess(invoice.lease_id, tenant_id)
        self.notifications = FakeNotifications()
        self.documents = FakeDocuments()
        self.committed = False

    async def __aenter__(self):
        self.snapshot = deepcopy((self.billing, self.notifications, self.documents))
        self.committed = False
        return self

    async def __aexit__(self, *args):
        if not self.committed:
            self.billing, self.notifications, self.documents = self.snapshot

    async def commit(self):
        self.committed = True


@pytest.fixture
def context():
    tenant = Actor(uuid4(), Role.TENANT)
    manager = Actor(uuid4(), Role.MANAGER)
    invoice = InvoiceDTO(
        id=uuid4(),
        lease_id=uuid4(),
        number="INV-1",
        description="Аренда",
        amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        status="pending",
        issued_on=date.today(),
        due_date=date.today(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    return SimpleNamespace(
        tenant=tenant, manager=manager, invoice=invoice, uow=FakeOfficeUow(invoice, tenant.id)
    )


def payment(amount="40.00", reference="bank-1"):
    return RecordPayment(amount=Decimal(amount), reference=reference, paid_on=date.today())


async def test_payment_replay_has_no_duplicate_balance_or_notification(context):
    use_case = RecordPaymentUseCase(context.uow)
    first = await use_case.execute(context.manager, context.invoice.id, payment())
    second = await use_case.execute(context.manager, context.invoice.id, payment())
    assert first.id == second.id
    assert context.uow.billing.invoice.paid_amount == Decimal("40.00")
    assert len(context.uow.notifications.items) == 1


async def test_payment_reference_cannot_change_amount(context):
    use_case = RecordPaymentUseCase(context.uow)
    await use_case.execute(context.manager, context.invoice.id, payment())
    with pytest.raises(Conflict):
        await use_case.execute(context.manager, context.invoice.id, payment("50.00"))
    assert context.uow.billing.invoice.paid_amount == Decimal("40.00")


async def test_tenant_cannot_record_payments(context):
    with pytest.raises(AccessDenied):
        await RecordPaymentUseCase(context.uow).execute(
            context.tenant, context.invoice.id, payment()
        )
    assert context.uow.billing.payments == {}


async def test_overpayment_and_missing_invoice_are_rejected(context):
    use_case = RecordPaymentUseCase(context.uow)
    with pytest.raises(BusinessRuleViolation):
        await use_case.execute(context.manager, context.invoice.id, payment("100.01"))
    with pytest.raises(EntityNotFound):
        await use_case.execute(context.manager, uuid4(), payment())
    assert context.uow.billing.payments == {}


async def test_future_payment_rejected(context):
    command = payment().model_copy(update={"paid_on": date.today() + timedelta(days=1)})
    with pytest.raises(BusinessRuleViolation):
        await RecordPaymentUseCase(context.uow).execute(
            context.manager, context.invoice.id, command
        )


async def test_notification_failure_rolls_back_payment(context):
    context.uow.notifications.fail = True
    with pytest.raises(RuntimeError):
        await RecordPaymentUseCase(context.uow).execute(
            context.manager, context.invoice.id, payment()
        )
    assert context.uow.billing.invoice.paid_amount == 0
    assert context.uow.billing.payments == {}


async def test_upload_normalizes_filename_and_records_notification(context):
    command = CreateDocument(
        lease_id=context.invoice.lease_id,
        title="Договор",
        kind=DocumentKind.CONTRACT,
        issued_on=date.today(),
    )
    document = await UploadDocumentUseCase(context.uow).execute(
        context.manager, command, "../../contract.pdf", b"%PDF-1.7\nexample"
    )
    assert document.filename == "contract.pdf"
    assert context.uow.notifications.items[0].user_id == context.tenant.id


async def test_foreign_tenant_cannot_download_document(context):
    command = CreateDocument(
        lease_id=context.invoice.lease_id,
        title="Договор",
        kind=DocumentKind.CONTRACT,
        issued_on=date.today(),
    )
    document = await UploadDocumentUseCase(context.uow).execute(
        context.manager, command, "contract.pdf", b"%PDF-1.7\nexample"
    )
    with pytest.raises(EntityNotFound):
        await DownloadDocumentUseCase(context.uow).execute(Actor(uuid4(), Role.TENANT), document.id)


@pytest.mark.parametrize("content", [b"", b"<html>not a PDF</html>"])
async def test_invalid_documents_are_not_saved(context, content):
    command = CreateDocument(
        lease_id=context.invoice.lease_id,
        title="Договор",
        kind=DocumentKind.CONTRACT,
        issued_on=date.today(),
    )
    with pytest.raises(BusinessRuleViolation):
        await UploadDocumentUseCase(context.uow).execute(
            context.manager, command, "contract.pdf", content
        )
    assert context.uow.documents.items == {}


async def test_disabled_payment_notifications_do_not_block_payment(context):
    context.uow.notifications.settings = NotificationPreferences(notify_payments=False)
    result = await RecordPaymentUseCase(context.uow).execute(
        context.manager, context.invoice.id, payment()
    )
    assert result.amount == Decimal("40.00")
    assert context.uow.billing.invoice.paid_amount == Decimal("40.00")
    assert context.uow.committed
    assert not context.uow.notifications.items


async def test_disabled_document_notifications_do_not_block_upload(context):
    context.uow.notifications.settings = NotificationPreferences(notify_documents=False)
    command = CreateDocument(
        lease_id=context.invoice.lease_id,
        title="Contract",
        kind=DocumentKind.CONTRACT,
        issued_on=date.today(),
    )
    result = await UploadDocumentUseCase(context.uow).execute(
        context.manager, command, "contract.pdf", b"%PDF-1.7\nexample"
    )
    assert result.id in context.uow.documents.items
    assert context.uow.committed
    assert not context.uow.notifications.items


@pytest.mark.parametrize(
    "category",
    [NotificationCategory.PAYMENTS, NotificationCategory.DOCUMENTS, NotificationCategory.METERS],
)
async def test_explicit_notification_cannot_bypass_disabled_category(context, category):
    context.uow.notifications.settings = NotificationPreferences(False, False, False)
    with pytest.raises(BusinessRuleViolation):
        await CreateNotificationUseCase(context.uow).execute(
            context.manager,
            CreateNotification(
                user_id=context.tenant.id, title="Title", body="Body", category=category
            ),
        )
    assert not context.uow.notifications.items


async def test_system_notifications_always_delivered(context):
    context.uow.notifications.settings = NotificationPreferences(False, False, False)
    result = await CreateNotificationUseCase(context.uow).execute(
        context.manager,
        CreateNotification(user_id=context.tenant.id, title="Title", body="Body"),
    )
    assert result.category == NotificationCategory.SYSTEM
    assert context.uow.notifications.items == [result]
    assert context.uow.committed
