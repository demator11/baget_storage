import asyncio
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from baget_storage.core.exceptions import AccessDenied, Conflict
from baget_storage.domain.entities.meter import MeterKind, ReadingStatus
from baget_storage.domain.entities.user import Actor, Role
from baget_storage.domain.use_cases.meter import (
    CorrectReadingUseCase,
    CreateMeterUseCase,
    GetMetersUseCase,
    GetReadingsUseCase,
    ReviewReadingUseCase,
    SubmitReadingUseCase,
)
from baget_storage.infrastructure.postgres.meter_uow import MeterSqlAlchemyUnitOfWork
from baget_storage.infrastructure.postgres.models.meter import MeterOrm, MeterReadingOrm
from baget_storage.infrastructure.postgres.models.office import NotificationOrm
from baget_storage.infrastructure.postgres.models.rental import LeaseOrm, RoomOrm
from baget_storage.infrastructure.postgres.models.user import UserOrm
from baget_storage.infrastructure.postgres.repository.office import SqlAlchemyNotificationRepository
from baget_storage.schemas.meter import (
    CorrectReading,
    CreateMeter,
    MeterDTO,
    MeterFilters,
    ReadingFilters,
    ReviewReading,
    SubmitReading,
)

pytestmark = pytest.mark.integration


class FixedMeterClock:
    def today(self) -> date:
        return date(2026, 11, 3)


@pytest.fixture
async def meter_context(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[Actor, Actor, MeterDTO]:
    manager = Actor(id=uuid4(), role=Role.MANAGER)
    tenant = Actor(id=uuid4(), role=Role.TENANT)
    room_id = uuid4()
    async with session_factory.begin() as session:
        session.add_all(
            [
                UserOrm(
                    id=manager.id,
                    email="meter-manager@example.com",
                    password_hash="unused-in-meter-tests",
                    full_name="Manager",
                    role="manager",
                ),
                UserOrm(
                    id=tenant.id,
                    email="meter-tenant@example.com",
                    password_hash="unused-in-meter-tests",
                    full_name="Tenant",
                    role="tenant",
                ),
                RoomOrm(
                    id=room_id,
                    name="B2",
                    building="B",
                    floor=1,
                    area_m2=250,
                    monthly_rent=10000,
                ),
            ]
        )
        await session.flush()
        session.add(
            LeaseOrm(
                number="METER-LEASE",
                room_id=room_id,
                tenant_id=tenant.id,
                start_date=date(2026, 9, 1),
                end_date=date(2027, 8, 31),
                monthly_rent=10000,
            )
        )
    meter = await CreateMeterUseCase(MeterSqlAlchemyUnitOfWork(session_factory)).execute(
        manager,
        CreateMeter(
            room_id=room_id,
            kind=MeterKind.WATER,
            serial_number="W-1",
            initial_reading="100",
        ),
    )
    return manager, tenant, meter


async def test_meter_reading_workflow_and_queries(session_factory, meter_context) -> None:
    manager, tenant, meter = meter_context
    assert meter.created_at.tzinfo is not None
    assert meter.unit == "m3"
    submit = SubmitReadingUseCase(MeterSqlAlchemyUnitOfWork(session_factory), FixedMeterClock())
    review = ReviewReadingUseCase(MeterSqlAlchemyUnitOfWork(session_factory))
    october = await submit.execute(
        tenant, meter.id, SubmitReading(period=date(2026, 10, 1), value="120")
    )
    rejected = await review.execute(
        manager,
        october.id,
        ReviewReading(status=ReadingStatus.REJECTED, reason="Please check the display"),
    )
    assert rejected.reviewed_by == manager.id
    assert rejected.reviewed_at.tzinfo is not None
    corrected = await submit.execute(
        tenant, meter.id, SubmitReading(period=date(2026, 10, 1), value="125")
    )
    assert corrected.id == october.id
    assert corrected.previous_value == Decimal("100")
    assert corrected.consumption == Decimal("25")
    assert corrected.reviewed_at is None
    assert corrected.rejection_reason is None
    await review.execute(manager, october.id, ReviewReading(status=ReadingStatus.ACCEPTED))
    november = await submit.execute(
        tenant, meter.id, SubmitReading(period=date(2026, 11, 1), value="135.125")
    )
    assert november.previous_value == Decimal("125")
    assert november.consumption == Decimal("10.125")
    meters = GetMetersUseCase(MeterSqlAlchemyUnitOfWork(session_factory), FixedMeterClock())
    latest = await meters.execute(tenant, MeterFilters())
    assert latest.total == 1
    assert latest.items[0].latest_reading_id == november.id
    assert latest.items[0].previous_accepted_value == Decimal("125")
    earlier = await meters.execute(
        manager, MeterFilters(period=date(2026, 10, 1), status=ReadingStatus.ACCEPTED)
    )
    assert earlier.total == 1
    assert earlier.items[0].latest_reading_id == october.id
    assert earlier.items[0].previous_accepted_value == Decimal("100")
    async with MeterSqlAlchemyUnitOfWork(session_factory) as details_uow:
        details = await details_uow.meters.get_by_id(meter.id)
    assert details.room_name == "B2"
    assert details.latest_reading_id == november.id
    assert details.previous_accepted_value == Decimal("125")
    history = GetReadingsUseCase(MeterSqlAlchemyUnitOfWork(session_factory), FixedMeterClock())
    first = await history.execute(tenant, meter.id, ReadingFilters(limit=1))
    second = await history.execute(tenant, meter.id, ReadingFilters(limit=1, offset=1))
    assert first.total == second.total == 2
    assert first.items[0].id == november.id
    assert second.items[0].id == october.id
    async with session_factory() as session:
        notifications = list(
            await session.scalars(
                select(NotificationOrm).where(NotificationOrm.user_id == tenant.id)
            )
        )
    assert len(notifications) == 2
    assert all(row.user_id == tenant.id and row.category == "meters" for row in notifications)


async def test_concurrent_meter_submissions_create_only_one_reading(
    session_factory, meter_context
) -> None:
    _, tenant, meter = meter_context
    results = await asyncio.gather(
        *[
            SubmitReadingUseCase(
                MeterSqlAlchemyUnitOfWork(session_factory), FixedMeterClock()
            ).execute(tenant, meter.id, SubmitReading(period=date(2026, 11, 1), value="120"))
            for _ in range(2)
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(result, Conflict) for result in results) == 1
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(MeterReadingOrm))
    assert count == 1


async def test_meter_access_is_scoped_to_current_lease(session_factory, meter_context) -> None:
    _, _, meter = meter_context
    unrelated = Actor(id=uuid4(), role=Role.TENANT)
    meters = GetMetersUseCase(MeterSqlAlchemyUnitOfWork(session_factory), FixedMeterClock())
    assert (await meters.execute(unrelated, MeterFilters())).total == 0
    with pytest.raises(AccessDenied):
        await SubmitReadingUseCase(
            MeterSqlAlchemyUnitOfWork(session_factory), FixedMeterClock()
        ).execute(unrelated, meter.id, SubmitReading(period=date(2026, 11, 1), value="120"))
    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(MeterReadingOrm))
    assert count == 0


async def test_duplicate_serial_rolls_back_and_uow_can_be_reused(
    session_factory, meter_context
) -> None:
    manager, _, meter = meter_context
    uow = MeterSqlAlchemyUnitOfWork(session_factory)
    use_case = CreateMeterUseCase(uow)
    data = CreateMeter(room_id=meter.room_id, kind=MeterKind.WATER, serial_number="W-1")
    with pytest.raises(Conflict):
        await use_case.execute(manager, data)
    created = await use_case.execute(manager, data.model_copy(update={"serial_number": "W-2"}))
    assert created.serial_number == "W-2"
    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(MeterOrm))
    assert count == 2


async def test_notification_failure_rolls_back_meter_review(
    session_factory, meter_context, monkeypatch
) -> None:
    manager, tenant, meter = meter_context
    reading = await SubmitReadingUseCase(
        MeterSqlAlchemyUnitOfWork(session_factory), FixedMeterClock()
    ).execute(tenant, meter.id, SubmitReading(period=date(2026, 11, 1), value="120"))

    async def fail_notification(self, data) -> None:
        raise RuntimeError("Notification storage failed")

    monkeypatch.setattr(SqlAlchemyNotificationRepository, "create", fail_notification)
    with pytest.raises(RuntimeError, match="Notification storage failed"):
        await ReviewReadingUseCase(MeterSqlAlchemyUnitOfWork(session_factory)).execute(
            manager, reading.id, ReviewReading(status=ReadingStatus.ACCEPTED)
        )
    async with session_factory() as session:
        stored = await session.get(MeterReadingOrm, reading.id)
        notifications = await session.scalar(
            select(func.count())
            .select_from(NotificationOrm)
            .where(NotificationOrm.user_id == tenant.id)
        )
    assert stored.status == "pending"
    assert stored.reviewed_by is None
    assert stored.reviewed_at is None
    assert notifications == 0


async def test_manager_correction_recovers_meter_after_tenant_turnover(
    session_factory, meter_context
) -> None:
    manager, former, meter = meter_context
    submit = SubmitReadingUseCase(MeterSqlAlchemyUnitOfWork(session_factory), FixedMeterClock())
    reading = await submit.execute(
        former, meter.id, SubmitReading(period=date(2026, 10, 1), value="120")
    )
    await ReviewReadingUseCase(MeterSqlAlchemyUnitOfWork(session_factory)).execute(
        manager, reading.id, ReviewReading(status=ReadingStatus.REJECTED, reason="Wrong value")
    )
    successor = Actor(id=uuid4(), role=Role.TENANT)
    async with session_factory.begin() as session:
        previous_lease = await session.scalar(
            select(LeaseOrm).where(LeaseOrm.tenant_id == former.id)
        )
        previous_lease.end_date = date(2026, 10, 31)
        await session.flush()
        session.add(
            UserOrm(
                id=successor.id,
                email="successor@example.com",
                full_name="Successor",
                password_hash="unused-in-meter-tests",
                role="tenant",
            )
        )
        await session.flush()
        session.add(
            LeaseOrm(
                number="SUCCESSOR-LEASE",
                room_id=meter.room_id,
                tenant_id=successor.id,
                start_date=date(2026, 11, 1),
                end_date=date(2027, 10, 31),
                monthly_rent=10000,
            )
        )
    with pytest.raises(Conflict):
        await submit.execute(
            successor, meter.id, SubmitReading(period=date(2026, 11, 1), value="150")
        )
    correction = await CorrectReadingUseCase(
        MeterSqlAlchemyUnitOfWork(session_factory), FixedMeterClock()
    ).execute(manager, reading.id, CorrectReading(value="125", reason="Handover inspection"))
    assert correction.status is ReadingStatus.ACCEPTED
    assert correction.submitted_by == former.id
    assert correction.reviewed_by == manager.id
    assert correction.reviewed_at.tzinfo is not None
    assert correction.correction_reason == "Handover inspection"
    following = await submit.execute(
        successor, meter.id, SubmitReading(period=date(2026, 11, 1), value="150")
    )
    assert following.previous_value == Decimal("125")
    assert following.consumption == Decimal("25")
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(NotificationOrm)
            .where(NotificationOrm.user_id == former.id)
        )
    assert count == 2


async def test_concurrent_corrections_accept_reading_only_once(
    session_factory, meter_context
) -> None:
    manager, tenant, meter = meter_context
    reading = await SubmitReadingUseCase(
        MeterSqlAlchemyUnitOfWork(session_factory), FixedMeterClock()
    ).execute(tenant, meter.id, SubmitReading(period=date(2026, 11, 1), value="120"))
    await ReviewReadingUseCase(MeterSqlAlchemyUnitOfWork(session_factory)).execute(
        manager, reading.id, ReviewReading(status=ReadingStatus.REJECTED, reason="Wrong value")
    )
    results = await asyncio.gather(
        *(
            CorrectReadingUseCase(
                MeterSqlAlchemyUnitOfWork(session_factory), FixedMeterClock()
            ).execute(manager, reading.id, CorrectReading(value="125", reason="Verified"))
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, Conflict) for result in results) == 1
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    async with session_factory() as session:
        stored = await session.get(MeterReadingOrm, reading.id)
        count = await session.scalar(
            select(func.count())
            .select_from(NotificationOrm)
            .where(NotificationOrm.user_id == tenant.id)
        )
    assert stored.status == "accepted"
    assert stored.value == Decimal("125")
    assert stored.correction_reason == "Verified"
    assert count == 2
