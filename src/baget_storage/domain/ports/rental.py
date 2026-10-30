from datetime import date
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

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


class RoomRepository(Protocol):
    async def create(self, data: CreateRoom) -> RoomDTO: ...

    async def get_by_id(self, entity_id: UUID) -> RoomDTO | None: ...

    async def get_for_update(self, entity_id: UUID) -> RoomDTO | None: ...

    async def update(self, entity_id: UUID, data: UpdateRoom) -> RoomDTO | None: ...

    async def list(
        self, filters: RoomFilter, tenant_id: UUID | None = None
    ) -> RentalPage[RoomDTO]: ...

    async def tenant_has_access(
        self, room_id: UUID, tenant_id: UUID, on_date: date | None = None
    ) -> bool: ...


class LeaseRepository(Protocol):
    async def create(self, data: CreateLease) -> LeaseDTO: ...

    async def get_by_id(self, entity_id: UUID) -> LeaseDTO | None: ...

    async def list(self, filters: LeaseFilter) -> RentalPage[LeaseDTO]: ...

    async def has_overlap(self, room_id: UUID, start_date: date, end_date: date) -> bool: ...

    async def tenant_exists(self, tenant_id: UUID) -> bool: ...

    async def terminate(self, entity_id: UUID) -> LeaseDTO | None: ...


class RentalUnitOfWork(Protocol):
    rooms: RoomRepository
    leases: LeaseRepository

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...
