from datetime import UTC, date, datetime
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import case, exists, func, or_, select, update
from sqlalchemy.sql.elements import Case
from sqlalchemy.sql.selectable import Exists

from baget_storage.domain.entities.rental import LeaseStatus, RoomStatus
from baget_storage.infrastructure.postgres.models.rental import LeaseOrm, RoomOrm
from baget_storage.infrastructure.postgres.models.user import UserOrm
from baget_storage.infrastructure.postgres.repository.base import BaseSqlAlchemyRepository
from baget_storage.schemas.rental import (
    CreateLease,
    CreateRoom,
    LeaseDTO,
    LeaseFilter,
    RentalPage,
    RoomDTO,
    RoomFilter,
    UpdateRoom,
)


class SqlAlchemyRoomRepository(BaseSqlAlchemyRepository[RoomOrm, CreateRoom, UpdateRoom, RoomDTO]):
    model = RoomOrm
    read_dto = RoomDTO

    @staticmethod
    def _occupied() -> Exists:
        today = datetime.now(UTC).date()
        return exists().where(
            LeaseOrm.room_id == RoomOrm.id,
            LeaseOrm.status == LeaseStatus.ACTIVE,
            LeaseOrm.start_date <= today,
            LeaseOrm.end_date >= today,
        )

    @classmethod
    def _status(cls) -> Case[str]:
        return case(
            (RoomOrm.status == RoomStatus.MAINTENANCE, RoomStatus.MAINTENANCE.value),
            (cls._occupied(), RoomStatus.OCCUPIED.value),
            else_=RoomStatus.AVAILABLE.value,
        )

    async def get_by_id(self, entity_id: UUID) -> RoomDTO | None:
        result = await self._session.execute(
            select(RoomOrm, self._status()).where(RoomOrm.id == entity_id)
        )
        row = result.first()
        if row is None:
            return None
        return self._to_dto(row[0]).model_copy(update={"status": RoomStatus(row[1])})

    async def get_for_update(self, entity_id: UUID) -> RoomDTO | None:
        row = await self._session.scalar(
            select(RoomOrm).where(RoomOrm.id == entity_id).with_for_update()
        )
        return await self.get_by_id(entity_id) if row is not None else None

    async def update(self, entity_id: UUID, data: UpdateRoom) -> RoomDTO | None:
        result = await super().update(entity_id, data)
        return await self.get_by_id(entity_id) if result is not None else None

    async def list(self, filters: RoomFilter, tenant_id: UUID | None = None) -> RentalPage[RoomDTO]:
        statement = select(RoomOrm, self._status())
        if tenant_id is not None:
            today = datetime.now(UTC).date()
            statement = statement.where(
                exists().where(
                    LeaseOrm.room_id == RoomOrm.id,
                    LeaseOrm.tenant_id == tenant_id,
                    LeaseOrm.status == LeaseStatus.ACTIVE,
                    LeaseOrm.start_date <= today,
                    LeaseOrm.end_date >= today,
                )
            )
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
        total = await self._session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = await self._session.execute(
            statement.order_by(RoomOrm.building, RoomOrm.name, RoomOrm.id)
            .offset(filters.offset)
            .limit(filters.limit)
        )
        items = [
            self._to_dto(row[0]).model_copy(update={"status": RoomStatus(row[1])}) for row in rows
        ]
        return RentalPage(items=items, total=total or 0, limit=filters.limit, offset=filters.offset)

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

    async def list(self, filters: LeaseFilter) -> RentalPage[LeaseDTO]:
        statement = select(LeaseOrm)
        for field in ("room_id", "tenant_id", "status"):
            value = getattr(filters, field)
            if value is not None:
                statement = statement.where(getattr(LeaseOrm, field) == value)
        total = await self._session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = await self._session.scalars(
            statement.order_by(LeaseOrm.start_date.desc(), LeaseOrm.id)
            .offset(filters.offset)
            .limit(filters.limit)
        )
        return RentalPage(
            items=[self._to_dto(row) for row in rows],
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
        return self._to_dto(row) if row is not None else None
