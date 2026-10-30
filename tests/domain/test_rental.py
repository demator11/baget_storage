from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
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
from baget_storage.domain.entities.rental import LeasePeriod, LeaseStatus, RoomStatus
from baget_storage.domain.entities.user import Actor, Role
from baget_storage.domain.use_cases.rental import (
    CreateLeaseUseCase,
    CreateRoomUseCase,
    GetLeasesUseCase,
    GetRoomsUseCase,
    GetRoomUseCase,
    TerminateLeaseUseCase,
    UpdateRoomUseCase,
)
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


class FakeRooms:
    def __init__(self, owner: FakeRentalUnitOfWork) -> None:
        self.owner = owner
        self.data: dict[UUID, RoomDTO] = {}

    async def create(self, data: CreateRoom) -> RoomDTO:
        now = datetime.now(UTC)
        result = RoomDTO(id=uuid4(), created_at=now, updated_at=now, **data.model_dump())
        self.data[result.id] = result
        return result

    async def get_by_id(self, entity_id: UUID) -> RoomDTO | None:
        return self.data.get(entity_id)

    async def get_for_update(self, entity_id: UUID) -> RoomDTO | None:
        return await self.get_by_id(entity_id)

    async def update(self, entity_id: UUID, data: UpdateRoom) -> RoomDTO | None:
        result = self.data.get(entity_id)
        if result is None:
            return None
        result = result.model_copy(update=data.model_dump(exclude_unset=True))
        self.data[entity_id] = result
        return result

    async def list(self, filters: RoomFilter, tenant_id: UUID | None = None) -> RentalPage[RoomDTO]:
        items = []
        for item in self.data.values():
            if tenant_id is not None and not await self.tenant_has_access(item.id, tenant_id):
                continue
            if filters.status is not None and item.status != filters.status:
                continue
            items.append(item)
        items.sort(key=lambda item: (item.building, item.name, item.id))
        return RentalPage(
            items=items[filters.offset : filters.offset + filters.limit],
            total=len(items),
            limit=filters.limit,
            offset=filters.offset,
        )

    async def tenant_has_access(
        self, room_id: UUID, tenant_id: UUID, on_date: date | None = None
    ) -> bool:
        day = on_date or datetime.now(UTC).date()
        return any(
            lease.room_id == room_id
            and lease.tenant_id == tenant_id
            and lease.status == LeaseStatus.ACTIVE
            and lease.start_date <= day <= lease.end_date
            for lease in self.owner.leases.data.values()
        )


class FakeLeases:
    def __init__(self) -> None:
        self.data: dict[UUID, LeaseDTO] = {}
        self.tenants: set[UUID] = set()

    async def create(self, data: CreateLease) -> LeaseDTO:
        now = datetime.now(UTC)
        result = LeaseDTO(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            status=LeaseStatus.ACTIVE,
            **data.model_dump(),
        )
        self.data[result.id] = result
        return result

    async def get_by_id(self, entity_id: UUID) -> LeaseDTO | None:
        return self.data.get(entity_id)

    async def list(self, filters: LeaseFilter) -> RentalPage[LeaseDTO]:
        items = [
            item
            for item in self.data.values()
            if (filters.tenant_id is None or item.tenant_id == filters.tenant_id)
            and (filters.room_id is None or item.room_id == filters.room_id)
            and (filters.status is None or item.status == filters.status)
        ]
        return RentalPage(
            items=items[filters.offset : filters.offset + filters.limit],
            total=len(items),
            limit=filters.limit,
            offset=filters.offset,
        )

    async def has_overlap(self, room_id: UUID, start_date: date, end_date: date) -> bool:
        period = LeasePeriod(start_date, end_date)
        return any(
            lease.room_id == room_id
            and lease.status == LeaseStatus.ACTIVE
            and period.overlaps(LeasePeriod(lease.start_date, lease.end_date))
            for lease in self.data.values()
        )

    async def tenant_exists(self, tenant_id: UUID) -> bool:
        return tenant_id in self.tenants

    async def terminate(self, entity_id: UUID) -> LeaseDTO | None:
        result = self.data.get(entity_id)
        if result is None:
            return None
        result = result.model_copy(update={"status": LeaseStatus.TERMINATED})
        self.data[entity_id] = result
        return result


class FakeRentalUnitOfWork:
    def __init__(self) -> None:
        self.rooms = FakeRooms(self)
        self.leases = FakeLeases()
        self.committed = False
        self.fail_commit = False

    async def __aenter__(self) -> Self:
        self.snapshot = deepcopy((self.rooms.data, self.leases.data))
        self.committed = False
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self.committed:
            self.rooms.data, self.leases.data = self.snapshot

    async def commit(self) -> None:
        if self.fail_commit:
            raise RuntimeError("Storage failure")
        self.committed = True


@pytest.fixture
def manager() -> Actor:
    return Actor(uuid4(), Role.MANAGER)


@pytest.fixture
def tenant() -> Actor:
    return Actor(uuid4(), Role.TENANT)


@pytest.fixture
def uow(tenant: Actor) -> FakeRentalUnitOfWork:
    value = FakeRentalUnitOfWork()
    value.leases.tenants.add(tenant.id)
    return value


@pytest.fixture
def room_data() -> CreateRoom:
    return CreateRoom(
        name="A-101",
        area_m2=Decimal("100.00"),
        building="Building A",
        floor=1,
        monthly_rent=Decimal("15000.00"),
        description="Loading area",
    )


@pytest.fixture
async def room(uow: FakeRentalUnitOfWork, manager: Actor, room_data: CreateRoom) -> RoomDTO:
    return await CreateRoomUseCase(uow).execute(manager, room_data)


@pytest.fixture
def lease_data(room: RoomDTO, tenant: Actor) -> CreateLease:
    today = datetime.now(UTC).date()
    return CreateLease(
        number="LEASE-001",
        room_id=room.id,
        tenant_id=tenant.id,
        start_date=today,
        end_date=today + timedelta(days=30),
        monthly_rent=room.monthly_rent,
    )


async def test_create_room_and_preserve_unset_fields(
    uow: FakeRentalUnitOfWork, manager: Actor, room: RoomDTO
) -> None:
    updated = await UpdateRoomUseCase(uow).execute(manager, room.id, UpdateRoom(description=None))
    assert updated.description is None
    assert updated.name == room.name
    assert updated.monthly_rent == room.monthly_rent
    assert uow.rooms.data[room.id] == updated


async def test_tenant_cannot_create_room(
    uow: FakeRentalUnitOfWork, tenant: Actor, room_data: CreateRoom
) -> None:
    with pytest.raises(AccessDenied):
        await CreateRoomUseCase(uow).execute(tenant, room_data)
    assert not uow.rooms.data


async def test_cannot_assign_occupied_status(
    uow: FakeRentalUnitOfWork, manager: Actor, room_data: CreateRoom
) -> None:
    with pytest.raises(BusinessRuleViolation):
        await CreateRoomUseCase(uow).execute(
            manager, room_data.model_copy(update={"status": RoomStatus.OCCUPIED})
        )
    assert not uow.rooms.data


async def test_missing_room_raises_not_found(uow: FakeRentalUnitOfWork, manager: Actor) -> None:
    with pytest.raises(EntityNotFound):
        await GetRoomUseCase(uow).execute(manager, uuid4())


async def test_create_lease_and_tenant_can_read_room(
    uow: FakeRentalUnitOfWork, manager: Actor, tenant: Actor, lease_data: CreateLease
) -> None:
    lease = await CreateLeaseUseCase(uow).execute(manager, lease_data)
    result = await GetRoomUseCase(uow).execute(tenant, lease.room_id)
    assert result.id == lease.room_id
    assert lease.status == LeaseStatus.ACTIVE


async def test_overlapping_lease_rejected_including_shared_boundary(
    uow: FakeRentalUnitOfWork, manager: Actor, lease_data: CreateLease
) -> None:
    await CreateLeaseUseCase(uow).execute(manager, lease_data)
    overlapping = lease_data.model_copy(
        update={"number": "LEASE-002", "start_date": lease_data.end_date}
    )
    with pytest.raises(Conflict):
        await CreateLeaseUseCase(uow).execute(manager, overlapping)
    assert len(uow.leases.data) == 1


async def test_future_nonoverlapping_lease_allowed(
    uow: FakeRentalUnitOfWork, manager: Actor, lease_data: CreateLease
) -> None:
    await CreateLeaseUseCase(uow).execute(manager, lease_data)
    future = lease_data.model_copy(
        update={
            "number": "LEASE-002",
            "start_date": lease_data.end_date + timedelta(days=1),
            "end_date": lease_data.end_date + timedelta(days=30),
        }
    )
    await CreateLeaseUseCase(uow).execute(manager, future)
    assert len(uow.leases.data) == 2


async def test_invalid_lease_dates_are_business_error(
    uow: FakeRentalUnitOfWork, manager: Actor, lease_data: CreateLease
) -> None:
    invalid = lease_data.model_copy(update={"end_date": lease_data.start_date - timedelta(days=1)})
    with pytest.raises(BusinessRuleViolation):
        await CreateLeaseUseCase(uow).execute(manager, invalid)
    assert not uow.leases.data


async def test_tenant_cannot_read_another_tenants_room(
    uow: FakeRentalUnitOfWork, room: RoomDTO
) -> None:
    other_tenant = Actor(uuid4(), Role.TENANT)
    with pytest.raises(EntityNotFound):
        await GetRoomUseCase(uow).execute(other_tenant, room.id)


async def test_tenant_lists_are_scoped_even_if_query_names_other_tenant(
    uow: FakeRentalUnitOfWork, manager: Actor, tenant: Actor, lease_data: CreateLease
) -> None:
    await CreateLeaseUseCase(uow).execute(manager, lease_data)
    result = await GetLeasesUseCase(uow).execute(tenant, LeaseFilter(tenant_id=uuid4()))
    assert len(result.items) == 1
    assert result.items[0].tenant_id == tenant.id
    other = Actor(uuid4(), Role.TENANT)
    rooms = await GetRoomsUseCase(uow).execute(other, RoomFilter())
    assert rooms.total == 0


async def test_terminate_is_idempotent_and_preserves_history(
    uow: FakeRentalUnitOfWork, manager: Actor, tenant: Actor, lease_data: CreateLease
) -> None:
    lease = await CreateLeaseUseCase(uow).execute(manager, lease_data)
    first = await TerminateLeaseUseCase(uow).execute(manager, lease.id)
    second = await TerminateLeaseUseCase(uow).execute(manager, lease.id)
    assert first == second
    assert first.status == LeaseStatus.TERMINATED
    assert len(uow.leases.data) == 1
    with pytest.raises(EntityNotFound):
        await GetRoomUseCase(uow).execute(tenant, lease.room_id)


async def test_storage_failure_rolls_back_lease(
    uow: FakeRentalUnitOfWork, manager: Actor, lease_data: CreateLease
) -> None:
    uow.fail_commit = True
    with pytest.raises(RuntimeError, match="Storage failure"):
        await CreateLeaseUseCase(uow).execute(manager, lease_data)
    assert not uow.leases.data


async def test_maintenance_room_cannot_be_leased(
    uow: FakeRentalUnitOfWork, manager: Actor, lease_data: CreateLease
) -> None:
    await UpdateRoomUseCase(uow).execute(
        manager, lease_data.room_id, UpdateRoom(status=RoomStatus.MAINTENANCE)
    )
    with pytest.raises(BusinessRuleViolation):
        await CreateLeaseUseCase(uow).execute(manager, lease_data)
    assert not uow.leases.data


async def test_room_with_future_booking_cannot_enter_maintenance(
    uow: FakeRentalUnitOfWork, manager: Actor, lease_data: CreateLease
) -> None:
    future = lease_data.model_copy(
        update={
            "start_date": lease_data.end_date + timedelta(days=1),
            "end_date": lease_data.end_date + timedelta(days=30),
        }
    )
    await CreateLeaseUseCase(uow).execute(manager, future)
    with pytest.raises(BusinessRuleViolation):
        await UpdateRoomUseCase(uow).execute(
            manager, future.room_id, UpdateRoom(status=RoomStatus.MAINTENANCE)
        )
    assert uow.rooms.data[future.room_id].status == RoomStatus.AVAILABLE
