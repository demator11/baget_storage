from uuid import UUID

from baget_storage.core.exceptions import (
    AccessDenied,
    BusinessRuleViolation,
    Conflict,
    EntityNotFound,
)
from baget_storage.domain.entities.meter import (
    ReadingStatus,
    validate_reading,
    validate_reading_period,
    validate_review,
)
from baget_storage.domain.entities.office import NotificationCategory, NotificationResource
from baget_storage.domain.entities.reading_period import ReadingPeriodPolicy
from baget_storage.domain.entities.user import Actor, Role
from baget_storage.domain.ports.meter import MeterClock, MeterUnitOfWork
from baget_storage.domain.use_cases.notifications import notify_managers, notify_user
from baget_storage.schemas.meter import (
    CorrectReading,
    CreateMeter,
    CreateReading,
    MeterDTO,
    MeterFilters,
    MeterPage,
    MeterStatsDTO,
    ReadingDTO,
    ReadingFilters,
    ReadingPage,
    ReadingPeriodFilter,
    ReadingPeriodPage,
    ReviewReading,
    SubmitReading,
)
from baget_storage.schemas.office import CreateNotification

DEFAULT_READING_POLICY = ReadingPeriodPolicy()


class CreateMeterUseCase:
    def __init__(self, uow: MeterUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, actor: Actor, data: CreateMeter) -> MeterDTO:
        if actor.role is not Role.MANAGER:
            raise AccessDenied()
        if data.initial_reading < 0 or not data.initial_reading.is_finite():
            raise BusinessRuleViolation("Initial reading must be nonnegative and finite.")
        async with self._uow:
            if not await self._uow.meters.room_exists(data.room_id):
                raise EntityNotFound("Room was not found.")
            meter = await self._uow.meters.create(data)
            await self._uow.commit()
            return meter


class GetMetersUseCase:
    def __init__(self, uow: MeterUnitOfWork, clock: MeterClock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(self, actor: Actor, filters: MeterFilters) -> MeterPage:
        validate_reading_period(filters.period)
        tenant_id = None if actor.role is Role.MANAGER else actor.id
        async with self._uow:
            return await self._uow.meters.list_meters(filters, tenant_id, self._clock.today())


class GetReadingsUseCase:
    def __init__(self, uow: MeterUnitOfWork, clock: MeterClock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(self, actor: Actor, meter_id: UUID, filters: ReadingFilters) -> ReadingPage:
        validate_reading_period(filters.period)
        async with self._uow:
            meter = await self._uow.meters.get_by_id(meter_id)
            if meter is None:
                raise EntityNotFound("Meter was not found.")
            if actor.role is not Role.MANAGER and not await self._uow.meters.tenant_has_access(
                meter.room_id, actor.id, self._clock.today()
            ):
                raise AccessDenied()
            tenant_id = None if actor.role is Role.MANAGER else actor.id
            return await self._uow.meters.list_readings(meter_id, filters, tenant_id)


class SubmitReadingUseCase:
    def __init__(
        self,
        uow: MeterUnitOfWork,
        clock: MeterClock,
        policy: ReadingPeriodPolicy = DEFAULT_READING_POLICY,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._policy = policy

    async def execute(self, actor: Actor, meter_id: UUID, data: SubmitReading) -> ReadingDTO:
        if actor.role is not Role.TENANT:
            raise AccessDenied("Only a tenant can submit a meter reading.")
        today = self._clock.today()
        async with self._uow:
            meter = await self._uow.meters.get_by_id(meter_id, lock=True)
            if meter is None:
                raise EntityNotFound("Meter was not found.")
            if not await self._uow.meters.tenant_has_access(
                meter.room_id, actor.id, today, data.period
            ):
                raise AccessDenied("The reading period must belong to your current lease.")
            latest = await self._uow.meters.latest_reading(meter_id)
            previous = latest.value if latest is not None else meter.initial_reading
            resubmitting = latest is not None and latest.period == data.period
            if latest is not None:
                if latest.period > data.period:
                    raise Conflict("Readings must be submitted in chronological order.")
                if resubmitting:
                    if latest.status is not ReadingStatus.REJECTED:
                        raise Conflict("A reading already exists for this month.")
                    if latest.submitted_by != actor.id:
                        raise AccessDenied("Only the original tenant can resubmit this reading.")
                    previous = latest.previous_value
                elif latest.status is not ReadingStatus.ACCEPTED:
                    raise Conflict("The previous reading must be accepted first.")
            validate_reading(data.period, data.value, previous, today)
            if not resubmitting and not self._policy.window(data.period, today).is_open:
                raise BusinessRuleViolation("The submission window for this period is closed.")
            if resubmitting and latest is not None:
                result = await self._uow.meters.resubmit_reading(latest.id, data.value)
            else:
                result = await self._uow.meters.create_reading(
                    CreateReading(
                        meter_id=meter_id,
                        submitted_by=actor.id,
                        period=data.period,
                        value=data.value,
                        previous_value=previous,
                    )
                )
            await notify_managers(
                self._uow.notifications,
                title="Новые показания счётчика",
                body=f"Переданы показания прибора {meter.serial_number} за {data.period:%m.%Y}.",
                category=NotificationCategory.METERS,
                resource_type=NotificationResource.READING,
                resource_id=result.id,
                event_key=f"reading-submitted:{result.id}:{result.updated_at.isoformat()}",
            )
            await self._uow.commit()
            return result


class GetReadingJournalUseCase:
    def __init__(self, uow: MeterUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, actor: Actor, filters: ReadingFilters) -> ReadingPage:
        validate_reading_period(filters.period)
        tenant_id = None if actor.role is Role.MANAGER else actor.id
        async with self._uow:
            return await self._uow.meters.list_readings(None, filters, tenant_id)


class GetReadingUseCase:
    def __init__(self, uow: MeterUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, actor: Actor, reading_id: UUID) -> ReadingDTO:
        async with self._uow:
            reading = await self._uow.meters.get_reading(reading_id)
            if reading is None:
                raise EntityNotFound("Reading was not found.")
            if actor.role is not Role.MANAGER and reading.submitted_by != actor.id:
                raise AccessDenied()
            return reading


class GetMeterUseCase:
    def __init__(self, uow: MeterUnitOfWork, clock: MeterClock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(self, actor: Actor, meter_id: UUID) -> MeterDTO:
        async with self._uow:
            meter = await self._uow.meters.get_by_id(meter_id)
            if meter is None:
                raise EntityNotFound("Meter was not found.")
            if actor.role is not Role.MANAGER and not await self._uow.meters.tenant_has_access(
                meter.room_id, actor.id, self._clock.today()
            ):
                raise AccessDenied()
            return meter


class GetMeterStatsUseCase:
    def __init__(self, uow: MeterUnitOfWork, clock: MeterClock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(self, actor: Actor, filters: MeterFilters) -> MeterStatsDTO:
        validate_reading_period(filters.period)
        tenant_id = None if actor.role is Role.MANAGER else actor.id
        async with self._uow:
            return await self._uow.meters.stats(filters, tenant_id, self._clock.today())


class GetReadingPeriodsUseCase:
    def __init__(
        self, uow: MeterUnitOfWork, clock: MeterClock, policy: ReadingPeriodPolicy
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._policy = policy

    async def execute(self, actor: Actor, filters: ReadingPeriodFilter) -> ReadingPeriodPage:
        tenant_id = None if actor.role is Role.MANAGER else actor.id
        today = self._clock.today()
        async with self._uow:
            periods, total = await self._uow.meters.periods(
                tenant_id, today, filters.limit, filters.offset
            )
            return ReadingPeriodPage(
                items=[self._policy.window(period, today) for period in periods],
                total=total,
                limit=filters.limit,
                offset=filters.offset,
            )


class ReviewReadingUseCase:
    def __init__(self, uow: MeterUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, actor: Actor, reading_id: UUID, data: ReviewReading) -> ReadingDTO:
        if actor.role is not Role.MANAGER:
            raise AccessDenied()
        validate_review(data.status, data.reason)
        async with self._uow:
            reading = await self._uow.meters.get_reading(reading_id)
            if reading is None:
                raise EntityNotFound("Reading was not found.")
            await self._uow.meters.get_by_id(reading.meter_id, lock=True)
            reading = await self._uow.meters.get_reading(reading_id)
            if reading is None:
                raise EntityNotFound("Reading was not found.")
            if reading.status is not ReadingStatus.PENDING:
                raise Conflict("Only pending readings can be reviewed.")
            result = await self._uow.meters.review_reading(reading_id, actor.id, data)
            body = f"Показания за {reading.period:%m.%Y} приняты."
            if data.status is ReadingStatus.REJECTED:
                body = f"Показания за {reading.period:%m.%Y} требуют уточнения: {data.reason}"
            await notify_user(
                self._uow.notifications,
                CreateNotification(
                    user_id=reading.submitted_by,
                    title="Показания проверены",
                    body=body,
                    category=NotificationCategory.METERS,
                    resource_type=NotificationResource.READING,
                    resource_id=reading.id,
                ),
            )
            await self._uow.commit()
            return result


class CorrectReadingUseCase:
    def __init__(self, uow: MeterUnitOfWork, clock: MeterClock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(self, actor: Actor, reading_id: UUID, data: CorrectReading) -> ReadingDTO:
        if actor.role is not Role.MANAGER:
            raise AccessDenied()
        if not data.reason.strip() or len(data.reason) > 1000:
            raise BusinessRuleViolation("A correction reason of 1 to 1000 characters is required.")
        async with self._uow:
            reading = await self._uow.meters.get_reading(reading_id)
            if reading is None:
                raise EntityNotFound("Reading was not found.")
            meter = await self._uow.meters.get_by_id(reading.meter_id, lock=True)
            if meter is None:
                raise EntityNotFound("Meter was not found.")
            latest = await self._uow.meters.latest_reading(meter.id)
            if (
                latest is None
                or latest.id != reading_id
                or latest.status is not ReadingStatus.REJECTED
            ):
                raise Conflict("Only the latest rejected reading can be corrected.")
            validate_reading(latest.period, data.value, latest.previous_value, self._clock.today())
            result = await self._uow.meters.correct_reading(reading_id, actor.id, data)
            await notify_user(
                self._uow.notifications,
                CreateNotification(
                    user_id=latest.submitted_by,
                    title="Показания скорректированы",
                    body=(
                        f"Показания за {latest.period:%m.%Y} исправлены управляющим "
                        f"на {result.value} и приняты. Причина: {data.reason.strip()}"
                    ),
                    category=NotificationCategory.METERS,
                    resource_type=NotificationResource.READING,
                    resource_id=latest.id,
                ),
            )
            await self._uow.commit()
            return result
