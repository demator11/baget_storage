from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import Select, and_, case, exists, func, or_, select, update
from sqlalchemy.sql.elements import Case

from baget_storage.domain.entities.rental import LeaseStatus, RoomStatus
from baget_storage.infrastructure.postgres.models.rental import LeaseOrm, RoomOrm
from baget_storage.infrastructure.postgres.models.user import UserOrm
from baget_storage.infrastructure.postgres.repository.base import BaseSqlAlchemyRepository
from baget_storage.schemas.references import LeaseRef, RoomRef, TenantRef
from baget_storage.schemas.rental import (
    ActiveLeaseDTO,
    CreateLease,
    CreateRoom,
    ExpiringLeaseFilter,
    LeaseDTO,
    LeaseFilter,
    RentalPage,
    RoomDTO,
    RoomFilter,
    RoomStatsDTO,
    UpdateRoom,
)


class SqlAlchemyRoomRepository(BaseSqlAlchemyRepository[RoomOrm, CreateRoom, UpdateRoom, RoomDTO]):
    model = RoomOrm
    read_dto = RoomDTO

    @classmethod
    def _status(cls) -> Case[str]:
        return case(
            (RoomOrm.status == RoomStatus.MAINTENANCE, RoomStatus.MAINTENANCE.value),
            (LeaseOrm.id.is_not(None), RoomStatus.OCCUPIED.value),
            else_=RoomStatus.AVAILABLE.value,
        )

    @classmethod
    def _query(cls, tenant_id: UUID | None = None) -> Select:
        today = datetime.now(UTC).date()
        statement = (
            select(RoomOrm, cls._status(), LeaseOrm, UserOrm)
            .select_from(RoomOrm)
            .outerjoin(
                LeaseOrm,
                and_(
                    LeaseOrm.room_id == RoomOrm.id,
                    LeaseOrm.status == LeaseStatus.ACTIVE,
                    LeaseOrm.start_date <= today,
                    LeaseOrm.end_date >= today,
                ),
            )
            .outerjoin(UserOrm, UserOrm.id == LeaseOrm.tenant_id)
        )
        if tenant_id is not None:
            statement = statement.where(LeaseOrm.tenant_id == tenant_id)
        return statement

    def _joined_dto(
        self, room: RoomOrm, status: str, lease: LeaseOrm | None, tenant: UserOrm | None
    ) -> RoomDTO:
        active_lease = None
        if lease is not None and tenant is not None:
            active_lease = ActiveLeaseDTO(
                **LeaseRef.model_validate(lease).model_dump(),
                tenant=TenantRef.model_validate(tenant),
            )
        return self._to_dto(room).model_copy(
            update={"status": RoomStatus(status), "active_lease": active_lease}
        )

    async def create(self, data: CreateRoom) -> RoomDTO:
        created = await super().create(data)
        return await self.get_by_id(created.id) or created

    async def get_by_id(self, entity_id: UUID) -> RoomDTO | None:
        result = await self._session.execute(self._query().where(RoomOrm.id == entity_id))
        row = result.first()
        if row is None:
            return None
        return self._joined_dto(*row)

    async def get_for_update(self, entity_id: UUID) -> RoomDTO | None:
        row = await self._session.scalar(
            select(RoomOrm).where(RoomOrm.id == entity_id).with_for_update()
        )
        return await self.get_by_id(entity_id) if row is not None else None

    async def update(self, entity_id: UUID, data: UpdateRoom) -> RoomDTO | None:
        result = await super().update(entity_id, data)
        return await self.get_by_id(entity_id) if result is not None else None

    async def list(self, filters: RoomFilter, tenant_id: UUID | None = None) -> RentalPage[RoomDTO]:
        statement = self._query(tenant_id)
        if filters.search:
            pattern = filters.search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            statement = statement.where(
                or_(
                    RoomOrm.name.ilike(f"%{pattern}%", escape="\\"),
                    RoomOrm.building.ilike(f"%{pattern}%", escape="\\"),
                )
            )
        if filters.status is not None:
            statement = statement.where(self._status() == filters.status.value)
        if filters.building is not None:
            statement = statement.where(RoomOrm.building == filters.building)
        total = await self._session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = await self._session.execute(
            statement.order_by(RoomOrm.building, RoomOrm.name, RoomOrm.id)
            .offset(filters.offset)
            .limit(filters.limit)
        )
        items = [self._joined_dto(*row) for row in rows]
        return RentalPage(items=items, total=total or 0, limit=filters.limit, offset=filters.offset)

    async def stats(self, tenant_id: UUID | None) -> RoomStatsDTO:
        rooms = (
            self._query(tenant_id)
            .with_only_columns(RoomOrm.id, RoomOrm.area_m2, self._status().label("status"))
            .subquery()
        )
        fields = [
            func.count().label("total"),
            func.coalesce(func.sum(rooms.c.area_m2), 0).label("total_area_m2"),
        ]
        for status in RoomStatus:
            condition = rooms.c.status == status.value
            fields.extend(
                (
                    func.count().filter(condition).label(status.value),
                    func.coalesce(func.sum(rooms.c.area_m2).filter(condition), 0).label(
                        f"{status.value}_area_m2"
                    ),
                )
            )
        values = (await self._session.execute(select(*fields).select_from(rooms))).mappings().one()
        return RoomStatsDTO(**values)

    async def tenant_has_access(
        self, room_id: UUID, tenant_id: UUID, on_date: date | None = None
    ) -> bool:
        day = on_date or datetime.now(UTC).date()
        return bool(
            await self._session.scalar(
                select(
                    exists().where(
                        LeaseOrm.room_id == room_id,
                        LeaseOrm.tenant_id == tenant_id,
                        LeaseOrm.status == LeaseStatus.ACTIVE,
                        LeaseOrm.start_date <= day,
                        LeaseOrm.end_date >= day,
                    )
                )
            )
        )


class SqlAlchemyLeaseRepository(
    BaseSqlAlchemyRepository[LeaseOrm, CreateLease, BaseModel, LeaseDTO]
):
    model = LeaseOrm
    read_dto = LeaseDTO

    @staticmethod
    def _query() -> Select:
        return (
            select(LeaseOrm, RoomOrm, UserOrm)
            .select_from(LeaseOrm)
            .join(RoomOrm, RoomOrm.id == LeaseOrm.room_id)
            .join(UserOrm, UserOrm.id == LeaseOrm.tenant_id)
        )

    def _joined_dto(self, lease: LeaseOrm, room: RoomOrm, tenant: UserOrm) -> LeaseDTO:
        return self._to_dto(lease).model_copy(
            update={
                "room": RoomRef.model_validate(room),
                "tenant": TenantRef.model_validate(tenant),
            }
        )

    async def get_by_id(self, entity_id: UUID) -> LeaseDTO | None:
        row = (await self._session.execute(self._query().where(LeaseOrm.id == entity_id))).first()
        return self._joined_dto(*row) if row is not None else None

    async def create(self, data: CreateLease) -> LeaseDTO:
        created = await super().create(data)
        return await self.get_by_id(created.id) or created

    async def list(self, filters: LeaseFilter) -> RentalPage[LeaseDTO]:
        statement = self._query()
        for field in ("room_id", "tenant_id", "status"):
            value = getattr(filters, field)
            if value is not None:
                statement = statement.where(getattr(LeaseOrm, field) == value)
        total = await self._session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = await self._session.execute(
            statement.order_by(LeaseOrm.start_date.desc(), LeaseOrm.id)
            .offset(filters.offset)
            .limit(filters.limit)
        )
        return RentalPage(
            items=[self._joined_dto(*row) for row in rows],
            total=total or 0,
            limit=filters.limit,
            offset=filters.offset,
        )

    async def expiring(
        self, filters: ExpiringLeaseFilter, tenant_id: UUID | None
    ) -> RentalPage[LeaseDTO]:
        today = datetime.now(UTC).date()
        statement = self._query().where(
            LeaseOrm.status == LeaseStatus.ACTIVE,
            LeaseOrm.end_date >= today,
            LeaseOrm.end_date <= today + timedelta(days=filters.within_days),
        )
        if tenant_id is not None:
            statement = statement.where(LeaseOrm.tenant_id == tenant_id)
        total = await self._session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = await self._session.execute(
            statement.order_by(LeaseOrm.end_date, LeaseOrm.id)
            .offset(filters.offset)
            .limit(filters.limit)
        )
        return RentalPage(
            items=[self._joined_dto(*row) for row in rows],
            total=total or 0,
            limit=filters.limit,
            offset=filters.offset,
        )

    async def has_overlap(self, room_id: UUID, start_date: date, end_date: date) -> bool:
        return bool(
            await self._session.scalar(
                select(
                    exists().where(
                        LeaseOrm.room_id == room_id,
                        LeaseOrm.status == LeaseStatus.ACTIVE,
                        LeaseOrm.start_date <= end_date,
                        LeaseOrm.end_date >= start_date,
                    )
                )
            )
        )

    async def tenant_exists(self, tenant_id: UUID) -> bool:
        return bool(
            await self._session.scalar(
                select(
                    exists().where(
                        UserOrm.id == tenant_id,
                        UserOrm.role == "tenant",
                        UserOrm.is_active.is_(True),
                    )
                )
            )
        )

    async def terminate(self, entity_id: UUID) -> LeaseDTO | None:
        row = await self._session.scalar(
            update(LeaseOrm)
            .where(LeaseOrm.id == entity_id)
            .values(status=LeaseStatus.TERMINATED)
            .returning(LeaseOrm)
            .execution_options(populate_existing=True)
        )
        return await self.get_by_id(entity_id) if row is not None else None
