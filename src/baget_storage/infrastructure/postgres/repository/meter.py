from calendar import monthrange
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Select, func, literal, select, union

from baget_storage.domain.entities.meter import ReadingStatus
from baget_storage.infrastructure.postgres.models.meter import MeterOrm, MeterReadingOrm
from baget_storage.infrastructure.postgres.models.rental import LeaseOrm, RoomOrm
from baget_storage.infrastructure.postgres.models.user import UserOrm
from baget_storage.infrastructure.postgres.repository.base import BaseSqlAlchemyRepository
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
    ReadingPhotoDTO,
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
            self._details_query()
            .where(MeterOrm.id == meter_id)
            .execution_options(populate_existing=True)
        )
        if lock:
            query = query.with_for_update(of=MeterOrm)
        row = (await self._session.execute(query)).one_or_none()
        return self._meter_dto(*row) if row is not None else None

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

    def _details_query(self, period: date | None = None) -> Select:
        latest_id = select(MeterReadingOrm.id).where(MeterReadingOrm.meter_id == MeterOrm.id)
        if period is not None:
            latest_id = latest_id.where(MeterReadingOrm.period == period)
        latest_id = latest_id.order_by(MeterReadingOrm.period.desc()).limit(1).correlate(MeterOrm)
        accepted = (
            select(MeterReadingOrm.value)
            .where(MeterReadingOrm.meter_id == MeterOrm.id, MeterReadingOrm.status == "accepted")
            .order_by(MeterReadingOrm.period.desc())
            .limit(1)
            .correlate(MeterOrm)
        )
        if period is not None:
            accepted = accepted.where(MeterReadingOrm.period < period)
        return (
            select(MeterOrm, MeterReadingOrm, RoomOrm, accepted.scalar_subquery())
            .join(RoomOrm, RoomOrm.id == MeterOrm.room_id)
            .outerjoin(MeterReadingOrm, MeterReadingOrm.id == latest_id.scalar_subquery())
        )

    def _meter_query(self, filters: MeterFilters, tenant_id: UUID | None, today: date) -> Select:
        period = filters.period
        if period is None and filters.status == "missing":
            period = today.replace(day=1)
        query = self._details_query(period)
        if filters.room_id is not None:
            query = query.where(MeterOrm.room_id == filters.room_id)
        if filters.kind is not None:
            query = query.where(MeterOrm.kind == filters.kind)
        if filters.status == "missing":
            query = query.where(MeterReadingOrm.id.is_(None))
        elif filters.status is not None:
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
        items = [self._meter_dto(*row) for row in rows]
        return MeterPage(items=items, total=total or 0, limit=filters.limit, offset=filters.offset)

    def _meter_dto(
        self,
        meter: MeterOrm,
        reading: MeterReadingOrm | None,
        room: RoomOrm,
        accepted: Decimal | None,
    ) -> MeterDTO:
        dto = self._to_dto(meter)
        dto.room_name = room.name
        dto.building = room.building
        dto.floor = room.floor
        dto.previous_accepted_value = accepted if accepted is not None else meter.initial_reading
        if reading is not None:
            dto.latest_reading_id = reading.id
            dto.latest_reading_value = reading.value
            dto.latest_reading_period = reading.period
            dto.latest_reading_status = ReadingStatus(reading.status)
            dto.latest_reading_at = reading.updated_at
            dto.latest_reading_has_photo = reading.has_photo
            dto.previous_accepted_value = reading.previous_value
        return dto

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
        row = (
            await self._session.execute(
                self._reading_details_query()
                .where(MeterReadingOrm.id == reading_id)
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        return self._reading_dto(*row) if row is not None else None

    @staticmethod
    def _reading_details_query() -> Select:
        return (
            select(MeterReadingOrm, MeterOrm, RoomOrm, UserOrm.full_name)
            .join(MeterOrm, MeterOrm.id == MeterReadingOrm.meter_id)
            .join(RoomOrm, RoomOrm.id == MeterOrm.room_id)
            .join(UserOrm, UserOrm.id == MeterReadingOrm.submitted_by)
        )

    @staticmethod
    def _reading_dto(
        reading: MeterReadingOrm, meter: MeterOrm, room: RoomOrm, name: str
    ) -> ReadingDTO:
        return ReadingDTO.model_validate(reading).model_copy(
            update={
                "room_id": room.id,
                "room_name": room.name,
                "kind": meter.kind,
                "serial_number": meter.serial_number,
                "unit": meter.unit,
                "submitted_by_name": name,
            }
        )

    async def create_reading(self, data: CreateReading) -> ReadingDTO:
        row = MeterReadingOrm(**data.model_dump())
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return ReadingDTO.model_validate(row)

    async def save_photo(self, reading_id: UUID, photo: ReadingPhotoDTO) -> ReadingDTO:
        row = await self._session.get(MeterReadingOrm, reading_id, populate_existing=True)
        if row is None:
            raise RuntimeError("The locked reading disappeared.")
        row.photo_content = photo.content
        row.photo_size = len(photo.content)
        await self._session.flush()
        await self._session.refresh(row)
        return ReadingDTO.model_validate(row)

    async def get_photo(self, reading_id: UUID) -> ReadingPhotoDTO | None:
        content = await self._session.scalar(
            select(MeterReadingOrm.photo_content).where(MeterReadingOrm.id == reading_id)
        )
        return ReadingPhotoDTO(content=content) if content is not None else None

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
        self, meter_id: UUID | None, filters: ReadingFilters, tenant_id: UUID | None = None
    ) -> ReadingPage:
        query = (
            select(MeterReadingOrm, MeterOrm, RoomOrm, UserOrm.full_name)
            .join(MeterOrm, MeterOrm.id == MeterReadingOrm.meter_id)
            .join(RoomOrm, RoomOrm.id == MeterOrm.room_id)
            .join(UserOrm, UserOrm.id == MeterReadingOrm.submitted_by)
        )
        if meter_id is not None:
            query = query.where(MeterReadingOrm.meter_id == meter_id)
        if filters.meter_id is not None:
            query = query.where(MeterReadingOrm.meter_id == filters.meter_id)
        if filters.room_id is not None:
            query = query.where(MeterOrm.room_id == filters.room_id)
        if filters.kind is not None:
            query = query.where(MeterOrm.kind == filters.kind)
        if filters.status is not None:
            query = query.where(MeterReadingOrm.status == filters.status)
        if filters.period is not None:
            query = query.where(MeterReadingOrm.period == filters.period)
        if tenant_id is not None:
            query = query.where(MeterReadingOrm.submitted_by == tenant_id)
        total = await self._session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await self._session.execute(
            query.order_by(MeterReadingOrm.period.desc(), MeterReadingOrm.id)
            .limit(filters.limit)
            .offset(filters.offset)
        )
        return ReadingPage(
            items=[
                ReadingDTO.model_validate(row).model_copy(
                    update={
                        "room_id": room.id,
                        "room_name": room.name,
                        "kind": meter.kind,
                        "serial_number": meter.serial_number,
                        "unit": meter.unit,
                        "submitted_by_name": name,
                    }
                )
                for row, meter, room, name in rows
            ],
            total=total or 0,
            limit=filters.limit,
            offset=filters.offset,
        )

    async def stats(
        self, filters: MeterFilters, tenant_id: UUID | None, today: date
    ) -> MeterStatsDTO:
        period = filters.period or today.replace(day=1)
        scoped = self._meter_query(
            filters.model_copy(update={"status": None, "period": period}), tenant_id, today
        )
        scoped = scoped.with_only_columns(MeterReadingOrm.status.label("reading_status")).subquery()
        result = (
            (
                await self._session.execute(
                    select(
                        func.count().label("total"),
                        func.count().filter(scoped.c.reading_status == "pending").label("pending"),
                        func.count()
                        .filter(scoped.c.reading_status == "accepted")
                        .label("accepted"),
                        func.count()
                        .filter(scoped.c.reading_status == "rejected")
                        .label("rejected"),
                        func.count().filter(scoped.c.reading_status.is_(None)).label("missing"),
                    ).select_from(scoped)
                )
            )
            .mappings()
            .one()
        )
        return MeterStatsDTO(period=period, **result)

    async def periods(
        self, tenant_id: UUID | None, today: date, limit: int, offset: int
    ) -> tuple[list[date], int]:
        current = today.replace(day=1)
        previous = date(current.year - (current.month == 1), (current.month - 2) % 12 + 1, 1)
        history = select(MeterReadingOrm.period)
        if tenant_id is not None:
            history = history.where(MeterReadingOrm.submitted_by == tenant_id)
        periods = union(history, select(literal(current)), select(literal(previous))).subquery()
        count = await self._session.scalar(select(func.count()).select_from(periods))
        values = await self._session.scalars(
            select(periods.c.period).order_by(periods.c.period.desc()).limit(limit).offset(offset)
        )
        return list(values), count or 0

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
