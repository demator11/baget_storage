from calendar import monthrange
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Select, func, select

from baget_storage.domain.entities.meter import ReadingStatus
from baget_storage.infrastructure.postgres.models.meter import MeterOrm, MeterReadingOrm
from baget_storage.infrastructure.postgres.models.rental import LeaseOrm, RoomOrm
from baget_storage.infrastructure.postgres.repository.base import BaseSqlAlchemyRepository
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
    UpdateMeter,
)


class SqlAlchemyMeterRepository(
    BaseSqlAlchemyRepository[MeterOrm, CreateMeter, UpdateMeter, MeterDTO]
):
    model = MeterOrm
    read_dto = MeterDTO

    async def get_by_id(self, meter_id: UUID, *, lock: bool = False) -> MeterDTO | None:
        query = (
            select(MeterOrm)
            .where(MeterOrm.id == meter_id)
            .execution_options(populate_existing=True)
        )
        if lock:
            query = query.with_for_update()
        row = await self._session.scalar(query)
        return self._to_dto(row) if row is not None else None

    async def room_exists(self, room_id: UUID) -> bool:
        return bool(
            await self._session.scalar(
                select(select(RoomOrm.id).where(RoomOrm.id == room_id).exists())
            )
        )

    async def tenant_has_access(
        self, room_id: UUID, tenant_id: UUID, today: date, period: date | None = None
    ) -> bool:
        query = select(LeaseOrm.id).where(
            LeaseOrm.room_id == room_id,
            LeaseOrm.tenant_id == tenant_id,
            LeaseOrm.status == "active",
            LeaseOrm.start_date <= today,
            LeaseOrm.end_date >= today,
        )
        if period is not None:
            period_end = period.replace(day=monthrange(period.year, period.month)[1])
            query = query.where(LeaseOrm.start_date <= period_end, LeaseOrm.end_date >= period)
        return bool(await self._session.scalar(select(query.exists())))

    def _meter_query(self, filters: MeterFilters, tenant_id: UUID | None, today: date) -> Select:
        latest_id = select(MeterReadingOrm.id).where(MeterReadingOrm.meter_id == MeterOrm.id)
        if filters.period is not None:
            latest_id = latest_id.where(MeterReadingOrm.period == filters.period)
        latest_id = latest_id.order_by(MeterReadingOrm.period.desc()).limit(1).correlate(MeterOrm)
        query = select(MeterOrm, MeterReadingOrm).outerjoin(
            MeterReadingOrm, MeterReadingOrm.id == latest_id.scalar_subquery()
        )
        if filters.room_id is not None:
            query = query.where(MeterOrm.room_id == filters.room_id)
        if filters.kind is not None:
            query = query.where(MeterOrm.kind == filters.kind)
        if filters.status is not None:
            query = query.where(MeterReadingOrm.status == filters.status)
        if tenant_id is not None:
            access = select(LeaseOrm.id).where(
                LeaseOrm.room_id == MeterOrm.room_id,
                LeaseOrm.tenant_id == tenant_id,
                LeaseOrm.status == "active",
                LeaseOrm.start_date <= today,
                LeaseOrm.end_date >= today,
            )
            query = query.where(access.exists())
        return query

    async def list_meters(
        self, filters: MeterFilters, tenant_id: UUID | None, today: date
    ) -> MeterPage:
        query = self._meter_query(filters, tenant_id, today)
        total = await self._session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await self._session.execute(
            query.order_by(MeterOrm.serial_number, MeterOrm.id)
            .limit(filters.limit)
            .offset(filters.offset)
        )
        items = []
        for meter, reading in rows:
            dto = self._to_dto(meter)
            if reading is not None:
                dto.latest_reading_id = reading.id
                dto.latest_reading_value = reading.value
                dto.latest_reading_period = reading.period
                dto.latest_reading_status = ReadingStatus(reading.status)
            items.append(dto)
        return MeterPage(items=items, total=total or 0, limit=filters.limit, offset=filters.offset)

    async def latest_reading(self, meter_id: UUID) -> ReadingDTO | None:
        row = await self._session.scalar(
            select(MeterReadingOrm)
            .where(MeterReadingOrm.meter_id == meter_id)
            .order_by(MeterReadingOrm.period.desc(), MeterReadingOrm.id)
            .limit(1)
            .execution_options(populate_existing=True)
        )
        return ReadingDTO.model_validate(row) if row is not None else None

    async def get_reading(self, reading_id: UUID) -> ReadingDTO | None:
        row = await self._session.scalar(
            select(MeterReadingOrm)
            .where(MeterReadingOrm.id == reading_id)
            .execution_options(populate_existing=True)
        )
        return ReadingDTO.model_validate(row) if row is not None else None

    async def create_reading(self, data: CreateReading) -> ReadingDTO:
        row = MeterReadingOrm(**data.model_dump())
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return ReadingDTO.model_validate(row)

    async def resubmit_reading(self, reading_id: UUID, value: Decimal) -> ReadingDTO:
        row = await self._session.get(MeterReadingOrm, reading_id, populate_existing=True)
        if row is None:
            raise RuntimeError("The locked reading disappeared.")
        row.value = value
        row.status = ReadingStatus.PENDING
        row.rejection_reason = None
        row.reviewed_by = None
        row.reviewed_at = None
        await self._session.flush()
        await self._session.refresh(row)
        return ReadingDTO.model_validate(row)

    async def review_reading(
        self, reading_id: UUID, reviewer_id: UUID, data: ReviewReading
    ) -> ReadingDTO:
        row = await self._session.get(MeterReadingOrm, reading_id, populate_existing=True)
        if row is None:
            raise RuntimeError("The locked reading disappeared.")
        row.status = data.status
        row.rejection_reason = data.reason if data.status is ReadingStatus.REJECTED else None
        row.reviewed_by = reviewer_id
        row.reviewed_at = func.now()
        await self._session.flush()
        await self._session.refresh(row)
        return ReadingDTO.model_validate(row)

    async def list_readings(
        self, meter_id: UUID, filters: ReadingFilters, tenant_id: UUID | None = None
    ) -> ReadingPage:
        query = select(MeterReadingOrm).where(MeterReadingOrm.meter_id == meter_id)
        if filters.status is not None:
            query = query.where(MeterReadingOrm.status == filters.status)
        if filters.period is not None:
            query = query.where(MeterReadingOrm.period == filters.period)
        if tenant_id is not None:
            query = query.where(MeterReadingOrm.submitted_by == tenant_id)
        total = await self._session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await self._session.scalars(
            query.order_by(MeterReadingOrm.period.desc(), MeterReadingOrm.id)
            .limit(filters.limit)
            .offset(filters.offset)
        )
        return ReadingPage(
            items=[ReadingDTO.model_validate(row) for row in rows],
            total=total or 0,
            limit=filters.limit,
            offset=filters.offset,
        )

    async def correct_reading(
        self, reading_id: UUID, reviewer_id: UUID, data: CorrectReading
    ) -> ReadingDTO:
        row = await self._session.get(MeterReadingOrm, reading_id, populate_existing=True)
        if row is None:
            raise RuntimeError("The locked reading disappeared.")
        row.value = data.value
        row.status = ReadingStatus.ACCEPTED
        row.correction_reason = data.reason.strip()
        row.rejection_reason = None
        row.reviewed_by = reviewer_id
        row.reviewed_at = func.now()
        await self._session.flush()
        await self._session.refresh(row)
        return ReadingDTO.model_validate(row)
