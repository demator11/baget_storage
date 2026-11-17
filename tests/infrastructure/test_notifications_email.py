import asyncio
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from baget_storage.api.auth import get_actor
from baget_storage.app import create_app
from baget_storage.core.config import Settings
from baget_storage.domain.entities.office import NotificationCategory
from baget_storage.domain.entities.reading_period import ReadingPeriodPolicy
from baget_storage.domain.entities.user import Actor, Role
from baget_storage.domain.use_cases.notifications import notify_user
from baget_storage.domain.use_cases.reminders import GenerateRemindersUseCase
from baget_storage.infrastructure.postgres.models.email import EmailOutboxOrm
from baget_storage.infrastructure.postgres.models.meter import MeterOrm, MeterReadingOrm
from baget_storage.infrastructure.postgres.models.office import (
    InvoiceOrm,
    NotificationOrm,
    PaymentOrm,
)
from baget_storage.infrastructure.postgres.models.rental import LeaseOrm, RoomOrm
from baget_storage.infrastructure.postgres.models.user import UserOrm
from baget_storage.infrastructure.postgres.office_uow import OfficeSqlAlchemyUnitOfWork
from baget_storage.infrastructure.postgres.reminder_uow import ReminderSqlAlchemyUnitOfWork
from baget_storage.infrastructure.postgres.repository.email import (
    SqlAlchemyEmailDeliveryStore,
    SqlAlchemyEmailOutbox,
)
from baget_storage.schemas.office import CreateNotification

pytestmark = pytest.mark.integration


class FixedReminderClock:
    def today(self):
        return date(2026, 12, 3)


@pytest.fixture
async def notification_user(session_factory):
    user_id = uuid4()
    async with session_factory.begin() as session:
        session.add(
            UserOrm(
                id=user_id,
                email="notification-tenant@example.com",
                full_name="Tenant",
                password_hash="unused-notification-test-password",
                role="tenant",
                email_verified_at=datetime.now(UTC),
            )
        )
    return user_id


async def test_in_app_deduplication_counts_and_email_preferences(
    session_factory, notification_user
):
    command = CreateNotification(
        user_id=notification_user,
        title="Invoice",
        body="Payment due",
        category=NotificationCategory.PAYMENTS,
        deduplication_key="invoice-due:test",
    )
    uow = OfficeSqlAlchemyUnitOfWork(session_factory)
    async with uow:
        first = await notify_user(uow.notifications, command)
        await uow.commit()
    async with uow:
        second = await notify_user(uow.notifications, command)
        await uow.commit()
    assert first.id == second.id
    async with session_factory.begin() as session:
        user = await session.get(UserOrm, notification_user)
        user.notify_payments = False
    async with uow:
        await notify_user(
            uow.notifications, command.model_copy(update={"deduplication_key": "invoice-due:next"})
        )
        await uow.commit()
    async with uow:
        counts = await uow.notifications.counts(notification_user)
        assert counts.total == counts.unread == 2
        assert counts.categories[NotificationCategory.PAYMENTS] == 2
        assert await uow.notifications.get_by_id(uuid4(), first.id) is None
        assert (await uow.notifications.get_by_id(notification_user, first.id)).id == first.id
        await uow.notifications.mark_read(notification_user, first.id)
        await uow.commit()
    async with uow:
        assert (await uow.notifications.counts(notification_user)).unread == 1
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(EmailOutboxOrm)) == 1


async def test_notification_and_email_queue_are_one_transaction(session_factory, notification_user):
    with pytest.raises(RuntimeError, match="rollback"):
        async with OfficeSqlAlchemyUnitOfWork(session_factory) as uow:
            await notify_user(
                uow.notifications,
                CreateNotification(user_id=notification_user, title="Title", body="Body"),
            )
            raise RuntimeError("rollback")
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(NotificationOrm)) == 0
        assert await session.scalar(select(func.count()).select_from(EmailOutboxOrm)) == 0


async def test_email_outbox_idempotent_claim_recovery_and_stale_ack(session_factory):
    now = datetime.now(UTC)
    async with session_factory.begin() as session:
        outbox = SqlAlchemyEmailOutbox(session)
        for _ in range(2):
            await outbox.enqueue(
                "tenant@example.com",
                "password_reset",
                {"url": "https://example.com/reset"},
                "reset:token",
                expires_at=now + timedelta(hours=1),
            )
    store = SqlAlchemyEmailDeliveryStore(session_factory)
    first, competing = await asyncio.gather(
        store.claim(now + timedelta(seconds=1), 1, 1, 3),
        store.claim(now + timedelta(seconds=1), 1, 1, 3),
    )
    claimed = first or competing
    assert len(first) + len(competing) == 1
    original = claimed[0]
    recovered = (await store.claim(now + timedelta(seconds=3), 1, 10, 3))[0]
    assert recovered.id == original.id
    assert recovered.claim_id != original.claim_id
    assert recovered.attempts == 2
    await store.mark_sent(original.id, original.claim_id, now)
    async with session_factory() as session:
        row = await session.get(EmailOutboxOrm, original.id)
        assert row.status == "processing"
    await store.mark_sent(recovered.id, recovered.claim_id, now)
    async with session_factory() as session:
        row = await session.get(EmailOutboxOrm, original.id)
        assert row.status == "sent"
        assert row.payload == {}


async def test_expired_and_exhausted_email_jobs_are_scrubbed(session_factory):
    now = datetime.now(UTC)
    async with session_factory.begin() as session:
        await SqlAlchemyEmailOutbox(session).enqueue(
            "tenant@example.com",
            "password_reset",
            {"url": "https://example.com/reset"},
            "expired:token",
            expires_at=now - timedelta(seconds=1),
        )
        session.add(
            EmailOutboxOrm(
                recipient="tenant@example.com",
                template="password_reset",
                payload={"url": "https://example.com/expired"},
                deduplication_key="exhausted:token",
                attempts=2,
                status="pending",
                expires_at=now + timedelta(hours=1),
            )
        )
    jobs = await SqlAlchemyEmailDeliveryStore(session_factory).claim(now, 10, 30, 2)
    assert jobs == []
    async with session_factory() as session:
        rows = list(await session.scalars(select(EmailOutboxOrm)))
        assert {row.status for row in rows} == {"expired", "failed"}
        assert all(row.payload == {} for row in rows)


async def test_scheduled_reminder_queries_deduplicate_and_skip_paid_or_submitted(
    session_factory, notification_user
):
    manager_id, room_id, lease_id, meter_id, invoice_id = [uuid4() for _ in range(5)]
    async with session_factory.begin() as session:
        session.add(
            UserOrm(
                id=manager_id,
                email="reminder-manager@example.com",
                full_name="Manager",
                password_hash="unused-reminder-test-password",
                role="manager",
            )
        )
        session.add(
            RoomOrm(
                id=room_id,
                name="Reminder room",
                building="A",
                floor=1,
                area_m2=100,
                monthly_rent=500,
            )
        )
        await session.flush()
        session.add(
            LeaseOrm(
                id=lease_id,
                room_id=room_id,
                tenant_id=notification_user,
                number="REMINDER-LEASE",
                start_date=date(2026, 11, 1),
                end_date=date(2026, 12, 20),
                monthly_rent=500,
            )
        )
        session.add(
            MeterOrm(
                id=meter_id,
                room_id=room_id,
                kind="water",
                serial_number="REMINDER-WATER",
                initial_reading=0,
            )
        )
        await session.flush()
        session.add(
            InvoiceOrm(
                id=invoice_id,
                lease_id=lease_id,
                number="REMINDER-INVOICE",
                description="Rent",
                amount=500,
                issued_on=date(2026, 12, 1),
                due_date=date(2026, 12, 4),
            )
        )
    use_case = GenerateRemindersUseCase(
        ReminderSqlAlchemyUnitOfWork(session_factory), FixedReminderClock(), ReadingPeriodPolicy()
    )
    first = await use_case.execute()
    await use_case.execute()
    assert first.invoices_checked == first.leases_checked == 1
    assert first.readings_checked == 2
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(NotificationOrm)) == 6
        assert await session.scalar(select(func.count()).select_from(EmailOutboxOrm)) == 5
        manager_events = list(
            await session.scalars(
                select(NotificationOrm).where(NotificationOrm.user_id == manager_id)
            )
        )
        assert len(manager_events) == 1
        assert manager_events[0].category == "leases"
    async with session_factory.begin() as session:
        session.add(
            PaymentOrm(
                invoice_id=invoice_id,
                amount=500,
                reference="REMINDER-PAID",
                paid_on=date(2026, 12, 3),
            )
        )
        session.add(
            MeterReadingOrm(
                meter_id=meter_id,
                submitted_by=notification_user,
                period=date(2026, 11, 1),
                value=10,
                previous_value=0,
                status="accepted",
                reviewed_by=manager_id,
                reviewed_at=datetime.now(UTC),
            )
        )
    async with ReminderSqlAlchemyUnitOfWork(session_factory) as uow:
        assert await uow.reminders.invoices_due(date(2026, 12, 3)) == []
        assert await uow.reminders.missing_readings(date(2026, 11, 1)) == []
        assert len(await uow.reminders.missing_readings(date(2026, 12, 1))) == 1


async def test_notification_detail_and_counters_api_is_scoped_to_authenticated_user(
    session_factory, test_database_url, notification_user
):
    async with OfficeSqlAlchemyUnitOfWork(session_factory) as uow:
        notification = await notify_user(
            uow.notifications,
            CreateNotification(
                user_id=notification_user,
                title="Invoice reminder",
                body="Due tomorrow",
                category=NotificationCategory.PAYMENTS,
            ),
        )
        await uow.commit()
    app = create_app(
        Settings(
            database_url=test_database_url,
            jwt_secret="notification-api-test-key-" * 2,
            environment="test",
        )
    )
    actor = Actor(id=notification_user, role=Role.TENANT)

    async def authenticated_actor():
        return actor

    app.dependency_overrides[get_actor] = authenticated_actor
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            detail = await client.get(f"/api/v1/notifications/{notification.id}")
            assert detail.status_code == 200, detail.text
            assert detail.json()["id"] == str(notification.id)
            summary = await client.get("/api/v1/notifications/summary")
            assert summary.status_code == 200, summary.text
            assert summary.json()["unread"] == 1
            assert summary.json()["categories"]["payments"] == 1
            actor = Actor(id=uuid4(), role=Role.MANAGER)
            other = await client.get(f"/api/v1/notifications/{notification.id}")
            assert other.status_code == 404
            assert (await client.get("/api/v1/notifications/summary")).json()["total"] == 0
