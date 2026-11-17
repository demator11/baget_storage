from calendar import monthrange
from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

import pytest

from baget_storage.core.exceptions import (
    AccessDenied,
    BusinessRuleViolation,
    Conflict,
    EntityNotFound,
)
from baget_storage.domain.entities.meter import MeterKind, ReadingStatus
from baget_storage.domain.entities.office import NotificationCategory, NotificationPreferences
from baget_storage.domain.entities.user import Actor, Role
from baget_storage.domain.use_cases.meter import (
    CorrectReadingUseCase,
    CreateMeterUseCase,
    GetMetersUseCase,
    GetReadingsUseCase,
    ReviewReadingUseCase,
    SubmitReadingUseCase,
)
from baget_storage.schemas.meter import (
    CorrectReading,
    CreateMeter,
    CreateReading,
    MeterDTO,
    MeterFilters,
    MeterPage,
    ReadingDTO,
    ReadingFilters,
    ReadingPage,
    ReviewReading,
    SubmitReading,
)
from baget_storage.schemas.office import CreateNotification, NotificationDTO

NOW = datetime(2026, 11, 17, 10, tzinfo=UTC)
PERIOD = date(2026, 11, 1)


class FakeClock:
    def today(self) -> date:
        return date(2026, 11, 3)


class FakeMeterRepository:
    def __init__(self) -> None:
        self.meters: dict[UUID, MeterDTO] = {}
        self.readings: dict[UUID, ReadingDTO] = {}
        self.rooms: set[UUID] = set()
        self.leases: dict[tuple[UUID, UUID], tuple[date, date]] = {}

    async def create(self, data: CreateMeter) -> MeterDTO:
        meter = MeterDTO(
            id=uuid4(), **data.model_dump(), unit=data.kind.unit, created_at=NOW, updated_at=NOW
        )
        self.meters[meter.id] = meter
        return meter

    async def get_by_id(self, meter_id: UUID, *, lock: bool = False) -> MeterDTO | None:
        return self.meters.get(meter_id)

    async def room_exists(self, room_id: UUID) -> bool:
        return room_id in self.rooms

    async def tenant_has_access(
        self, room_id: UUID, tenant_id: UUID, today: date, period: date | None = None
    ) -> bool:
        lease = self.leases.get((room_id, tenant_id))
        if lease is None or not lease[0] <= today <= lease[1]:
            return False
        if period is not None:
            period_end = period.replace(day=monthrange(period.year, period.month)[1])
            return lease[0] <= period_end and lease[1] >= period
        return True

    async def list_meters(
        self, filters: MeterFilters, tenant_id: UUID | None, today: date
    ) -> MeterPage:
        items = [
            meter
            for meter in self.meters.values()
            if tenant_id is None or await self.tenant_has_access(meter.room_id, tenant_id, today)
        ]
        items.sort(key=lambda meter: (meter.serial_number, meter.id))
        return MeterPage(
            items=items[filters.offset : filters.offset + filters.limit],
            total=len(items),
            limit=filters.limit,
            offset=filters.offset,
        )

    async def latest_reading(self, meter_id: UUID) -> ReadingDTO | None:
        rows = [reading for reading in self.readings.values() if reading.meter_id == meter_id]
        return max(rows, key=lambda row: row.period) if rows else None

    async def get_reading(self, reading_id: UUID) -> ReadingDTO | None:
        return self.readings.get(reading_id)

    async def create_reading(self, data: CreateReading) -> ReadingDTO:
        reading = ReadingDTO(
            id=uuid4(),
            **data.model_dump(),
            consumption=data.value - data.previous_value,
            status=ReadingStatus.PENDING,
            rejection_reason=None,
            reviewed_by=None,
            reviewed_at=None,
            created_at=NOW,
            updated_at=NOW,
        )
        self.readings[reading.id] = reading
        return reading

    async def resubmit_reading(self, reading_id: UUID, value: Decimal) -> ReadingDTO:
        previous = self.readings[reading_id]
        result = previous.model_copy(
            update={
                "value": value,
                "consumption": value - previous.previous_value,
                "status": ReadingStatus.PENDING,
                "rejection_reason": None,
                "reviewed_by": None,
                "reviewed_at": None,
            }
        )
        self.readings[reading_id] = result
        return result

    async def review_reading(
        self, reading_id: UUID, reviewer_id: UUID, data: ReviewReading
    ) -> ReadingDTO:
        result = self.readings[reading_id].model_copy(
            update={
                "status": data.status,
                "rejection_reason": data.reason if data.status is ReadingStatus.REJECTED else None,
                "reviewed_by": reviewer_id,
                "reviewed_at": NOW,
            }
        )
        self.readings[reading_id] = result
        return result

    async def correct_reading(
        self, reading_id: UUID, reviewer_id: UUID, data: CorrectReading
    ) -> ReadingDTO:
        previous = self.readings[reading_id]
        result = previous.model_copy(
            update={
                "value": data.value,
                "consumption": data.value - previous.previous_value,
                "status": ReadingStatus.ACCEPTED,
                "correction_reason": data.reason.strip(),
                "rejection_reason": None,
                "reviewed_by": reviewer_id,
                "reviewed_at": NOW,
            }
        )
        self.readings[reading_id] = result
        return result

    async def list_readings(
        self, meter_id: UUID, filters: ReadingFilters, tenant_id: UUID | None = None
    ) -> ReadingPage:
        items = [
            reading
            for reading in self.readings.values()
            if reading.meter_id == meter_id
            and (tenant_id is None or reading.submitted_by == tenant_id)
        ]
        items.sort(key=lambda item: (item.period, item.id), reverse=True)
        return ReadingPage(
            items=items[filters.offset : filters.offset + filters.limit],
            total=len(items),
            limit=filters.limit,
            offset=filters.offset,
        )


class FakeMeterNotifications:
    def __init__(self) -> None:
        self.items: list[NotificationDTO] = []
        self.fail_create = False
        self.settings = NotificationPreferences()

    async def manager_ids(self, *, after=None, limit=100) -> list[UUID]:
        return []

    async def verified_email(self, user_id: UUID) -> str | None:
        return None

    async def preferences(self, user_id: UUID) -> NotificationPreferences:
        return self.settings

    async def create(self, data: CreateNotification) -> NotificationDTO:
        if self.fail_create:
            raise RuntimeError("Notification failed")
        notification = NotificationDTO(
            id=uuid4(), **data.model_dump(), read_at=None, created_at=NOW
        )
        self.items.append(notification)
        return notification


class FakeMeterUnitOfWork:
    def __init__(self) -> None:
        self.meters = FakeMeterRepository()
        self.notifications = FakeMeterNotifications()
        self.committed = False
        self.fail_commit = False

    async def __aenter__(self) -> Self:
        self._snapshot = deepcopy(self.meters)
        self._notifications_snapshot = deepcopy(self.notifications)
        self.committed = False
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc is not None or not self.committed:
            self.meters = self._snapshot
            self.notifications = self._notifications_snapshot

    async def commit(self) -> None:
        if self.fail_commit:
            raise RuntimeError("Commit failed")
        self.committed = True


@pytest.fixture
def manager() -> Actor:
    return Actor(id=uuid4(), role=Role.MANAGER)


@pytest.fixture
def tenant() -> Actor:
    return Actor(id=uuid4(), role=Role.TENANT)


@pytest.fixture
async def meter_setup(tenant: Actor, manager: Actor) -> tuple[FakeMeterUnitOfWork, MeterDTO]:
    uow = FakeMeterUnitOfWork()
    room_id = uuid4()
    uow.meters.rooms.add(room_id)
    uow.meters.leases[(room_id, tenant.id)] = (date(2026, 9, 1), date(2027, 8, 31))
    meter = await CreateMeterUseCase(uow).execute(
        manager,
        CreateMeter(
            room_id=room_id, kind=MeterKind.WATER, serial_number="W-101", initial_reading="100"
        ),
    )
    return uow, meter


async def test_submit_review_and_next_month(meter_setup, tenant, manager) -> None:
    uow, meter = meter_setup
    use_case = SubmitReadingUseCase(uow, FakeClock())
    october = await use_case.execute(
        tenant, meter.id, SubmitReading(period=date(2026, 10, 1), value="125")
    )
    accepted = await ReviewReadingUseCase(uow).execute(
        manager, october.id, ReviewReading(status=ReadingStatus.ACCEPTED)
    )
    assert accepted.reviewed_by == manager.id
    assert len(uow.notifications.items) == 1
    assert uow.notifications.items[0].user_id == tenant.id
    assert uow.notifications.items[0].category is NotificationCategory.METERS
    november = await use_case.execute(
        tenant, meter.id, SubmitReading(period=PERIOD, value="160.125")
    )
    assert november.previous_value == Decimal("125")
    assert november.consumption == Decimal("35.125")
    assert len(uow.meters.readings) == 2


async def test_closed_period_rejects_new_reading_but_allows_requested_correction(
    meter_setup, tenant, manager
) -> None:
    uow, meter = meter_setup
    original = await SubmitReadingUseCase(uow, FakeClock()).execute(
        tenant, meter.id, SubmitReading(period=date(2026, 10, 1), value="120")
    )
    await ReviewReadingUseCase(uow).execute(
        manager, original.id, ReviewReading(status=ReadingStatus.REJECTED, reason="Check display")
    )
    late_clock = FakeClock()
    late_clock.today = lambda: date(2026, 12, 17)
    corrected = await SubmitReadingUseCase(uow, late_clock).execute(
        tenant, meter.id, SubmitReading(period=date(2026, 10, 1), value="125")
    )
    assert corrected.id == original.id
    await ReviewReadingUseCase(uow).execute(
        manager, original.id, ReviewReading(status=ReadingStatus.ACCEPTED)
    )
    with pytest.raises(BusinessRuleViolation, match="window"):
        await SubmitReadingUseCase(uow, late_clock).execute(
            tenant, meter.id, SubmitReading(period=date(2026, 11, 1), value="130")
        )
    assert len(uow.meters.readings) == 1


async def test_disabled_meter_notification_does_not_block_review(
    meter_setup, tenant, manager
) -> None:
    uow, meter = meter_setup
    uow.notifications.settings = NotificationPreferences(notify_meters=False)
    reading = await SubmitReadingUseCase(uow, FakeClock()).execute(
        tenant, meter.id, SubmitReading(period=PERIOD, value="125")
    )
    result = await ReviewReadingUseCase(uow).execute(
        manager, reading.id, ReviewReading(status=ReadingStatus.ACCEPTED)
    )
    assert result.status == ReadingStatus.ACCEPTED
    assert uow.meters.readings[reading.id].status == ReadingStatus.ACCEPTED
    assert uow.committed
    assert len(uow.notifications.items) == 1


async def test_tenant_cannot_create_meter(meter_setup, tenant) -> None:
    uow, meter = meter_setup
    with pytest.raises(AccessDenied):
        await CreateMeterUseCase(uow).execute(
            tenant, CreateMeter(room_id=meter.room_id, kind=MeterKind.WATER, serial_number="W-102")
        )
    assert len(uow.meters.meters) == 1


async def test_unknown_room_and_meter(meter_setup, tenant, manager) -> None:
    uow, _ = meter_setup
    with pytest.raises(EntityNotFound):
        await CreateMeterUseCase(uow).execute(
            manager, CreateMeter(room_id=uuid4(), kind=MeterKind.WATER, serial_number="W-102")
        )
    with pytest.raises(EntityNotFound):
        await SubmitReadingUseCase(uow, FakeClock()).execute(
            tenant, uuid4(), SubmitReading(period=PERIOD, value="120")
        )
    assert not uow.meters.readings


async def test_unrelated_tenant_is_denied(meter_setup) -> None:
    uow, meter = meter_setup
    actor = Actor(id=uuid4(), role=Role.TENANT)
    with pytest.raises(AccessDenied):
        await SubmitReadingUseCase(uow, FakeClock()).execute(
            actor, meter.id, SubmitReading(period=PERIOD, value="120")
        )
    with pytest.raises(AccessDenied):
        await GetReadingsUseCase(uow, FakeClock()).execute(actor, meter.id, ReadingFilters())
    assert not uow.meters.readings


@pytest.mark.parametrize(
    ("period", "value"),
    [(PERIOD, "99"), (date(2026, 12, 1), "120"), (date(2026, 11, 2), "120")],
)
async def test_invalid_reading_is_not_persisted(meter_setup, tenant, period, value) -> None:
    uow, meter = meter_setup
    with pytest.raises(BusinessRuleViolation):
        await SubmitReadingUseCase(uow, FakeClock()).execute(
            tenant, meter.id, SubmitReading(period=period, value=value)
        )
    assert not uow.meters.readings


async def test_period_before_current_lease_is_denied(meter_setup, tenant) -> None:
    uow, meter = meter_setup
    with pytest.raises(AccessDenied):
        await SubmitReadingUseCase(uow, FakeClock()).execute(
            tenant, meter.id, SubmitReading(period=date(2026, 8, 1), value="120")
        )
    assert not uow.meters.readings


async def test_duplicate_and_unreviewed_next_month_conflict(meter_setup, tenant) -> None:
    uow, meter = meter_setup
    use_case = SubmitReadingUseCase(uow, FakeClock())
    await use_case.execute(tenant, meter.id, SubmitReading(period=date(2026, 10, 1), value="120"))
    with pytest.raises(Conflict):
        await use_case.execute(
            tenant, meter.id, SubmitReading(period=date(2026, 10, 1), value="125")
        )
    with pytest.raises(Conflict):
        await use_case.execute(tenant, meter.id, SubmitReading(period=PERIOD, value="130"))
    assert len(uow.meters.readings) == 1


async def test_rejected_reading_can_be_corrected(meter_setup, tenant, manager) -> None:
    uow, meter = meter_setup
    use_case = SubmitReadingUseCase(uow, FakeClock())
    original = await use_case.execute(tenant, meter.id, SubmitReading(period=PERIOD, value="120"))
    await ReviewReadingUseCase(uow).execute(
        manager,
        original.id,
        ReviewReading(status=ReadingStatus.REJECTED, reason="Check the display"),
    )
    result = await use_case.execute(tenant, meter.id, SubmitReading(period=PERIOD, value="125"))
    assert result.id == original.id
    assert result.previous_value == Decimal("100")
    assert result.consumption == Decimal("25")
    assert result.status is ReadingStatus.PENDING
    assert result.rejection_reason is None
    assert result.reviewed_by is None
    assert len(uow.meters.readings) == 1


async def test_review_requires_manager_and_rejection_reason(meter_setup, tenant, manager) -> None:
    uow, meter = meter_setup
    reading = await SubmitReadingUseCase(uow, FakeClock()).execute(
        tenant, meter.id, SubmitReading(period=PERIOD, value="120")
    )
    use_case = ReviewReadingUseCase(uow)
    with pytest.raises(AccessDenied):
        await use_case.execute(tenant, reading.id, ReviewReading(status=ReadingStatus.ACCEPTED))
    with pytest.raises(BusinessRuleViolation):
        await use_case.execute(manager, reading.id, ReviewReading(status=ReadingStatus.REJECTED))
    with pytest.raises(BusinessRuleViolation):
        await use_case.execute(manager, reading.id, ReviewReading(status=ReadingStatus.PENDING))
    assert uow.meters.readings[reading.id].status is ReadingStatus.PENDING


async def test_accepted_reading_cannot_be_reviewed_again(meter_setup, tenant, manager) -> None:
    uow, meter = meter_setup
    reading = await SubmitReadingUseCase(uow, FakeClock()).execute(
        tenant, meter.id, SubmitReading(period=PERIOD, value="120")
    )
    use_case = ReviewReadingUseCase(uow)
    await use_case.execute(manager, reading.id, ReviewReading(status=ReadingStatus.ACCEPTED))
    with pytest.raises(Conflict):
        await use_case.execute(
            manager, reading.id, ReviewReading(status=ReadingStatus.REJECTED, reason="Change")
        )
    assert uow.meters.readings[reading.id].status is ReadingStatus.ACCEPTED


async def test_failed_commit_rolls_back_reading(meter_setup, tenant) -> None:
    uow, meter = meter_setup
    uow.fail_commit = True
    with pytest.raises(RuntimeError, match="Commit failed"):
        await SubmitReadingUseCase(uow, FakeClock()).execute(
            tenant, meter.id, SubmitReading(period=PERIOD, value="120")
        )
    assert not uow.meters.readings


@pytest.mark.parametrize("failure", ["notification", "commit"])
async def test_review_and_notification_roll_back_together(
    meter_setup, tenant, manager, failure
) -> None:
    uow, meter = meter_setup
    reading = await SubmitReadingUseCase(uow, FakeClock()).execute(
        tenant, meter.id, SubmitReading(period=PERIOD, value="120")
    )
    uow.notifications.fail_create = failure == "notification"
    uow.fail_commit = failure == "commit"
    with pytest.raises(RuntimeError):
        await ReviewReadingUseCase(uow).execute(
            manager, reading.id, ReviewReading(status=ReadingStatus.ACCEPTED)
        )
    assert uow.meters.readings[reading.id].status is ReadingStatus.PENDING
    assert not uow.notifications.items


async def test_meter_list_is_scoped_to_current_tenant(meter_setup, tenant, manager) -> None:
    uow, _ = meter_setup
    use_case = GetMetersUseCase(uow, FakeClock())
    own = await use_case.execute(tenant, MeterFilters())
    other = await use_case.execute(Actor(id=uuid4(), role=Role.TENANT), MeterFilters())
    all_meters = await use_case.execute(manager, MeterFilters())
    assert own.total == all_meters.total == 1
    assert other.total == 0


async def test_reading_history_does_not_expose_former_tenant(meter_setup, tenant, manager) -> None:
    uow, meter = meter_setup
    reading = await SubmitReadingUseCase(uow, FakeClock()).execute(
        tenant, meter.id, SubmitReading(period=PERIOD, value="120")
    )
    new_tenant = Actor(id=uuid4(), role=Role.TENANT)
    uow.meters.leases[(meter.room_id, new_tenant.id)] = (date(2026, 11, 1), date(2027, 1, 1))
    own = await GetReadingsUseCase(uow, FakeClock()).execute(tenant, meter.id, ReadingFilters())
    later = await GetReadingsUseCase(uow, FakeClock()).execute(
        new_tenant, meter.id, ReadingFilters()
    )
    assert [row.id for row in own.items] == [reading.id]
    assert later.items == []


async def test_manager_correction_unblocks_readings_after_tenant_turnover(
    meter_setup, tenant, manager
) -> None:
    uow, meter = meter_setup
    submit = SubmitReadingUseCase(uow, FakeClock())
    reading = await submit.execute(
        tenant, meter.id, SubmitReading(period=date(2026, 10, 1), value="120")
    )
    await ReviewReadingUseCase(uow).execute(
        manager, reading.id, ReviewReading(status=ReadingStatus.REJECTED, reason="Incorrect value")
    )
    uow.meters.leases[(meter.room_id, tenant.id)] = (date(2026, 9, 1), date(2026, 10, 31))
    successor = Actor(id=uuid4(), role=Role.TENANT)
    uow.meters.leases[(meter.room_id, successor.id)] = (date(2026, 11, 1), date(2027, 10, 31))
    with pytest.raises(Conflict):
        await submit.execute(successor, meter.id, SubmitReading(period=PERIOD, value="150"))
    corrected = await CorrectReadingUseCase(uow, FakeClock()).execute(
        manager, reading.id, CorrectReading(value="125", reason="Verified during handover")
    )
    assert corrected.status is ReadingStatus.ACCEPTED
    assert corrected.submitted_by == tenant.id
    assert corrected.reviewed_by == manager.id
    assert corrected.correction_reason == "Verified during handover"
    assert corrected.rejection_reason is None
    assert corrected.consumption == Decimal("25")
    assert uow.notifications.items[-1].user_id == tenant.id
    assert "Verified during handover" in uow.notifications.items[-1].body
    following = await submit.execute(successor, meter.id, SubmitReading(period=PERIOD, value="150"))
    assert following.previous_value == Decimal("125")
    assert following.consumption == Decimal("25")


async def test_correction_requires_manager_and_rejected_reading(
    meter_setup, tenant, manager
) -> None:
    uow, meter = meter_setup
    reading = await SubmitReadingUseCase(uow, FakeClock()).execute(
        tenant, meter.id, SubmitReading(period=PERIOD, value="120")
    )
    use_case = CorrectReadingUseCase(uow, FakeClock())
    command = CorrectReading(value="125", reason="Verified")
    with pytest.raises(AccessDenied):
        await use_case.execute(tenant, reading.id, command)
    with pytest.raises(Conflict):
        await use_case.execute(manager, reading.id, command)
    with pytest.raises(EntityNotFound):
        await use_case.execute(manager, uuid4(), command)
    assert uow.meters.readings[reading.id].status is ReadingStatus.PENDING
    assert not uow.notifications.items


async def test_only_latest_rejected_reading_can_be_corrected(meter_setup, tenant, manager) -> None:
    uow, meter = meter_setup
    submit = SubmitReadingUseCase(uow, FakeClock())
    older = await submit.execute(
        tenant, meter.id, SubmitReading(period=date(2026, 10, 1), value="120")
    )
    await ReviewReadingUseCase(uow).execute(
        manager, older.id, ReviewReading(status=ReadingStatus.ACCEPTED)
    )
    await submit.execute(tenant, meter.id, SubmitReading(period=PERIOD, value="150"))
    uow.meters.readings[older.id] = uow.meters.readings[older.id].model_copy(
        update={"status": ReadingStatus.REJECTED, "rejection_reason": "Imported legacy rejection"}
    )
    with pytest.raises(Conflict):
        await CorrectReadingUseCase(uow, FakeClock()).execute(
            manager, older.id, CorrectReading(value="130", reason="Verified")
        )
    assert uow.meters.readings[older.id].value == Decimal("120")


@pytest.mark.parametrize(
    ("value", "reason"), [("99", "Verified"), ("NaN", "Verified"), ("125", " ")]
)
async def test_invalid_manager_correction_does_not_change_reading(
    meter_setup, tenant, manager, value, reason
) -> None:
    uow, meter = meter_setup
    reading = await SubmitReadingUseCase(uow, FakeClock()).execute(
        tenant, meter.id, SubmitReading(period=PERIOD, value="120")
    )
    await ReviewReadingUseCase(uow).execute(
        manager, reading.id, ReviewReading(status=ReadingStatus.REJECTED, reason="Wrong value")
    )
    with pytest.raises(BusinessRuleViolation):
        await CorrectReadingUseCase(uow, FakeClock()).execute(
            manager, reading.id, CorrectReading.model_construct(value=Decimal(value), reason=reason)
        )
    assert uow.meters.readings[reading.id].value == Decimal("120")
    assert uow.meters.readings[reading.id].status is ReadingStatus.REJECTED
    assert len(uow.notifications.items) == 1


@pytest.mark.parametrize("failure", ["notification", "commit"])
async def test_correction_and_notification_roll_back_together(
    meter_setup, tenant, manager, failure
) -> None:
    uow, meter = meter_setup
    reading = await SubmitReadingUseCase(uow, FakeClock()).execute(
        tenant, meter.id, SubmitReading(period=PERIOD, value="120")
    )
    await ReviewReadingUseCase(uow).execute(
        manager, reading.id, ReviewReading(status=ReadingStatus.REJECTED, reason="Wrong value")
    )
    uow.notifications.fail_create = failure == "notification"
    uow.fail_commit = failure == "commit"
    with pytest.raises(RuntimeError):
        await CorrectReadingUseCase(uow, FakeClock()).execute(
            manager, reading.id, CorrectReading(value="125", reason="Verified")
        )
    assert uow.meters.readings[reading.id].status is ReadingStatus.REJECTED
    assert uow.meters.readings[reading.id].correction_reason is None
    assert uow.meters.readings[reading.id].value == Decimal("120")
    assert len(uow.notifications.items) == 1
