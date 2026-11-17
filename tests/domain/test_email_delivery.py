from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from baget_storage.domain.entities.email import EmailDeliveryError, EmailRetryPolicy
from baget_storage.domain.use_cases.email import DeliverEmailsUseCase
from baget_storage.schemas.email import EmailJob


class FakeClock:
    def __init__(self):
        self.value = datetime(2026, 11, 17, 12, tzinfo=UTC)

    def now(self):
        return self.value


class FakeEmailStore:
    def __init__(self, clock):
        self.clock = clock
        self.id = uuid4()
        self.available_at = clock.now()
        self.state = "pending"
        self.attempts = 0
        self.payload = {"title": "Due", "body": "Pay invoice"}
        self.error = None
        self.fail_mark_sent = False

    async def claim(self, now, limit, lease_seconds, max_attempts):
        if self.state != "pending" or self.available_at > now:
            return []
        self.state = "processing"
        self.attempts += 1
        return [
            EmailJob(
                id=self.id,
                recipient="tenant@example.com",
                template="notification",
                payload=self.payload,
                attempts=self.attempts,
                claim_id=uuid4(),
                expires_at=None,
            )
        ]

    async def mark_sent(self, job_id, claim_id, now):
        if self.fail_mark_sent:
            raise RuntimeError("Database unavailable after SMTP acceptance")
        self.state = "sent"
        self.payload = {}

    async def mark_failed(self, job_id, claim_id, error_code, available_at, permanent):
        self.state = "failed" if permanent else "pending"
        self.available_at = available_at
        self.error = error_code
        if permanent:
            self.payload = {}


class FakeSender:
    def __init__(self):
        self.error = None
        self.sent = []

    async def send(self, job):
        if self.error is not None:
            raise self.error
        self.sent.append(job)


@pytest.fixture
def delivery():
    clock = FakeClock()
    store = FakeEmailStore(clock)
    sender = FakeSender()
    use_case = DeliverEmailsUseCase(
        store, sender, clock, EmailRetryPolicy(max_attempts=2, base_delay_seconds=30)
    )
    return use_case, store, sender, clock


async def test_delivery_persists_completion_and_scrubs_payload(delivery):
    use_case, store, sender, _ = delivery
    result = await use_case.execute()
    assert result.sent == 1
    assert store.state == "sent"
    assert store.payload == {}
    assert len(sender.sent) == 1
    assert (await use_case.execute()).claimed == 0


async def test_transient_failure_retries_only_after_backoff(delivery):
    use_case, store, sender, clock = delivery
    sender.error = EmailDeliveryError("smtp_unavailable")
    first = await use_case.execute()
    assert first.retrying == 1
    assert store.available_at == clock.now() + timedelta(seconds=30)
    assert store.payload
    assert (await use_case.execute()).claimed == 0
    clock.value += timedelta(seconds=31)
    sender.error = None
    result = await use_case.execute()
    assert result.sent == 1
    assert sender.sent[0].id == store.id
    assert sender.sent[0].attempts == 2


async def test_permanent_failure_is_not_retried(delivery):
    use_case, store, sender, clock = delivery
    sender.error = EmailDeliveryError("recipient_refused", permanent=True)
    result = await use_case.execute()
    assert result.failed == 1
    assert store.state == "failed"
    assert store.payload == {}
    clock.value += timedelta(days=1)
    assert (await use_case.execute()).claimed == 0


async def test_retry_budget_is_finite(delivery):
    use_case, store, sender, clock = delivery
    sender.error = EmailDeliveryError("smtp_unavailable")
    await use_case.execute()
    clock.value += timedelta(minutes=1)
    result = await use_case.execute()
    assert result.failed == 1
    assert store.attempts == 2
    assert store.state == "failed"


async def test_raw_delivery_error_does_not_store_secret_exception_text(delivery):
    use_case, store, sender, _ = delivery
    sender.error = RuntimeError("smtp-password=private")
    await use_case.execute()
    assert store.error == "delivery_error"


async def test_successful_smtp_with_failed_database_ack_is_not_misreported(delivery):
    use_case, store, sender, _ = delivery
    store.fail_mark_sent = True
    with pytest.raises(RuntimeError, match="Database unavailable"):
        await use_case.execute()
    assert len(sender.sent) == 1
    assert store.state == "processing"
