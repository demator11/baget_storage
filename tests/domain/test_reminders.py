from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from baget_storage.domain.entities.office import NotificationCategory, NotificationPreferences
from baget_storage.domain.entities.reading_period import ReadingPeriodPolicy
from baget_storage.domain.use_cases.reminders import GenerateRemindersUseCase
from baget_storage.schemas.office import NotificationDTO
from baget_storage.schemas.reminders import InvoiceReminder, LeaseReminder, ReadingReminder


class FakeClock:
    def __init__(self):
        self.value = date(2026, 12, 3)

    def today(self):
        return self.value


class FakeReminderNotifications:
    def __init__(self):
        self.items = {}
        self.managers = [uuid4()]
        self.fail = False

    async def preferences(self, user_id):
        return NotificationPreferences()

    async def verified_email(self, user_id):
        return None

    async def manager_ids(self, after=None, limit=100):
        return sorted(item for item in self.managers if after is None or item > after)[:limit]

    async def create(self, data):
        if self.fail:
            raise RuntimeError("Notification store failed")
        if data.deduplication_key not in self.items:
            self.items[data.deduplication_key] = NotificationDTO(
                **data.model_dump(), id=uuid4(), created_at=datetime.now(UTC), read_at=None
            )
        return self.items[data.deduplication_key]


class FakeReminderRepository:
    def __init__(self, today):
        self.tenant = uuid4()
        self.invoices = [
            InvoiceReminder(
                id=uuid4(),
                user_id=self.tenant,
                number="DUE",
                due_date=today + timedelta(days=2),
                remaining_amount="100",
            ),
            InvoiceReminder(
                id=uuid4(),
                user_id=self.tenant,
                number="OVERDUE",
                due_date=today - timedelta(days=2),
                remaining_amount="50",
            ),
        ]
        self.leases = [
            LeaseReminder(
                id=uuid4(), user_id=self.tenant, number="LEASE", end_date=today + timedelta(days=10)
            )
        ]
        self.meter = ReadingReminder(meter_id=uuid4(), user_id=self.tenant, serial_number="W-1")
        self.periods_requested = []

    async def invoices_due(self, today, after=None, limit=100):
        return sorted(
            [row for row in self.invoices if after is None or row.id > after],
            key=lambda row: row.id,
        )[:limit]

    async def leases_ending(self, today, after=None, limit=100):
        return sorted(
            [row for row in self.leases if after is None or row.id > after], key=lambda row: row.id
        )[:limit]

    async def missing_readings(self, period, after=None, limit=100):
        self.periods_requested.append(period)
        return [self.meter] if after is None else []


class FakeReminderUow:
    def __init__(self, today):
        self.notifications = FakeReminderNotifications()
        self.reminders = FakeReminderRepository(today)
        self.committed = False

    async def __aenter__(self):
        self.snapshot = deepcopy(self.notifications)
        self.committed = False
        return self

    async def __aexit__(self, *args):
        if not self.committed:
            self.notifications = self.snapshot

    async def commit(self):
        self.committed = True


async def test_scheduled_reminders_are_idempotent_and_include_manager_audience():
    clock = FakeClock()
    uow = FakeReminderUow(clock.today())
    use_case = GenerateRemindersUseCase(uow, clock, ReadingPeriodPolicy())
    first = await use_case.execute()
    await use_case.execute()
    assert first.invoices_checked == 2
    assert first.leases_checked == 1
    assert first.readings_checked == 2
    assert len(uow.notifications.items) == 7
    lease_events = [
        item
        for item in uow.notifications.items.values()
        if item.category == NotificationCategory.LEASES
    ]
    assert {item.user_id for item in lease_events} == {
        uow.reminders.tenant,
        *uow.notifications.managers,
    }
    assert all(item.resource_id is not None for item in uow.notifications.items.values())


async def test_overdue_event_is_distinct_and_closed_reading_window_is_not_reminded():
    clock = FakeClock()
    uow = FakeReminderUow(clock.today())
    use_case = GenerateRemindersUseCase(uow, clock, ReadingPeriodPolicy())
    await use_case.execute()
    uow.reminders.periods_requested.clear()
    clock.value = date(2026, 12, 6)
    await use_case.execute()
    assert len(uow.notifications.items) == 8
    assert date(2026, 11, 1) not in uow.reminders.periods_requested
    assert date(2026, 12, 1) in uow.reminders.periods_requested


async def test_failed_reminder_transaction_is_retryable_without_partial_notifications():
    clock = FakeClock()
    uow = FakeReminderUow(clock.today())
    uow.notifications.fail = True
    use_case = GenerateRemindersUseCase(uow, clock, ReadingPeriodPolicy())
    with pytest.raises(RuntimeError, match="Notification store failed"):
        await use_case.execute()
    assert not uow.notifications.items
    uow.notifications.fail = False
    await use_case.execute()
    assert len(uow.notifications.items) == 7
