from datetime import UTC, date, datetime
from uuid import UUID

from baget_storage.core.exceptions import (
    AccessDenied,
    BusinessRuleViolation,
    Conflict,
    EntityNotFound,
)
from baget_storage.domain.entities.rental import (
    LeasePeriod,
    LeaseStatus,
    RoomStatus,
    validate_monthly_rent,
    validate_room_status,
)
from baget_storage.domain.entities.user import Actor, Role
from baget_storage.domain.ports.rental import RentalUnitOfWork
from baget_storage.schemas.rental import (
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


def require_manager(actor: Actor) -> None:
    if actor.role != Role.MANAGER:
        raise AccessDenied()


class CreateRoomUseCase:
    def __init__(self, uow: RentalUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, actor: Actor, data: CreateRoom) -> RoomDTO:
        require_manager(actor)
        validate_room_status(data.status)
        validate_monthly_rent(data.monthly_rent)
        async with self._uow as uow:
            result = await uow.rooms.create(data)
            await uow.commit()
            return result


class GetRoomUseCase:
    def __init__(self, uow: RentalUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, actor: Actor, room_id: UUID) -> RoomDTO:
        async with self._uow as uow:
            if actor.role != Role.MANAGER and not await uow.rooms.tenant_has_access(
                room_id, actor.id
            ):
                raise EntityNotFound()
            result = await uow.rooms.get_by_id(room_id)
            if result is None:
                raise EntityNotFound()
            return result


class GetRoomsUseCase:
    def __init__(self, uow: RentalUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, actor: Actor, filters: RoomFilter) -> RentalPage[RoomDTO]:
        tenant_id = actor.id if actor.role != Role.MANAGER else None
        async with self._uow as uow:
            return await uow.rooms.list(filters, tenant_id)


class UpdateRoomUseCase:
    def __init__(self, uow: RentalUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, actor: Actor, room_id: UUID, data: UpdateRoom) -> RoomDTO:
        require_manager(actor)
        if data.status is not None:
            validate_room_status(data.status)
        if data.monthly_rent is not None:
            validate_monthly_rent(data.monthly_rent)
        async with self._uow as uow:
            room = await uow.rooms.get_for_update(room_id)
            if room is None:
                raise EntityNotFound()
            if data.status == RoomStatus.MAINTENANCE and await uow.leases.has_overlap(
                room_id, datetime.now(UTC).date(), date.max
            ):
                raise BusinessRuleViolation(
                    "A room with a current or future lease cannot be put under maintenance."
                )
            result = await uow.rooms.update(room_id, data)
            if result is None:
                raise EntityNotFound()
            await uow.commit()
            return result


class CreateLeaseUseCase:
    def __init__(self, uow: RentalUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, actor: Actor, data: CreateLease) -> LeaseDTO:
        require_manager(actor)
        LeasePeriod(data.start_date, data.end_date)
        validate_monthly_rent(data.monthly_rent)
        async with self._uow as uow:
            room = await uow.rooms.get_for_update(data.room_id)
            if room is None:
                raise EntityNotFound("Room not found.")
            if room.status == RoomStatus.MAINTENANCE:
                raise BusinessRuleViolation("A room under maintenance cannot be leased.")
            if not await uow.leases.tenant_exists(data.tenant_id):
                raise EntityNotFound("Tenant not found.")
            if await uow.leases.has_overlap(data.room_id, data.start_date, data.end_date):
                raise Conflict("An active lease already covers this room and date range.")
            result = await uow.leases.create(data)
            await uow.commit()
            return result


class GetLeasesUseCase:
    def __init__(self, uow: RentalUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, actor: Actor, filters: LeaseFilter) -> RentalPage[LeaseDTO]:
        if actor.role != Role.MANAGER:
            filters = filters.model_copy(update={"tenant_id": actor.id})
        async with self._uow as uow:
            return await uow.leases.list(filters)


class TerminateLeaseUseCase:
    def __init__(self, uow: RentalUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, actor: Actor, lease_id: UUID) -> LeaseDTO:
        require_manager(actor)
        async with self._uow as uow:
            lease = await uow.leases.get_by_id(lease_id)
            if lease is None:
                raise EntityNotFound()
            await uow.rooms.get_for_update(lease.room_id)
            if lease.status == LeaseStatus.TERMINATED:
                return lease
            result = await uow.leases.terminate(lease_id)
            if result is None:
                raise EntityNotFound()
            await uow.commit()
            return result


class GetLeaseUseCase:
    def __init__(self, uow: RentalUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, actor: Actor, lease_id: UUID) -> LeaseDTO:
        async with self._uow as uow:
            lease = await uow.leases.get_by_id(lease_id)
            if lease is None or (actor.role == Role.TENANT and lease.tenant_id != actor.id):
                raise EntityNotFound("Lease not found.")
            return lease


class GetRoomStatsUseCase:
    def __init__(self, uow: RentalUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, actor: Actor) -> RoomStatsDTO:
        async with self._uow as uow:
            return await uow.rooms.stats(actor.id if actor.role == Role.TENANT else None)


class GetExpiringLeasesUseCase:
    def __init__(self, uow: RentalUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, actor: Actor, filters: ExpiringLeaseFilter) -> RentalPage[LeaseDTO]:
        async with self._uow as uow:
            return await uow.leases.expiring(
                filters, actor.id if actor.role == Role.TENANT else None
            )
