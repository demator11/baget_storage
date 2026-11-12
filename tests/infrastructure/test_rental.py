import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from baget_storage.core.exceptions import BusinessRuleViolation, Conflict, EntityNotFound
from baget_storage.domain.entities.rental import LeaseStatus, RoomStatus
from baget_storage.domain.entities.user import Actor, Role
from baget_storage.domain.use_cases.rental import (
    CreateLeaseUseCase,
    CreateRoomUseCase,
    GetRoomsUseCase,
    GetRoomUseCase,
    TerminateLeaseUseCase,
    UpdateRoomUseCase,
)
from baget_storage.infrastructure.postgres.models.rental import LeaseOrm
from baget_storage.infrastructure.postgres.rental_uow import RentalSqlAlchemyUnitOfWork
from baget_storage.infrastructure.postgres.repository.rental import (
    SqlAlchemyLeaseRepository,
    SqlAlchemyRoomRepository,
)
from baget_storage.infrastructure.postgres.repository.user import SqlAlchemyUserRepository
from baget_storage.schemas.rental import CreateLease, CreateRoom, LeaseDTO, RoomFilter, UpdateRoom
from baget_storage.schemas.user import StoreUser

pytestmark = pytest.mark.integration


@pytest.fixture
def manager() -> Actor:
    return Actor(uuid4(), Role.MANAGER)


@pytest.fixture
async def lease_data(
    session_factory: async_sessionmaker[AsyncSession], manager: Actor
) -> CreateLease:
    async with session_factory() as session:
        user = await SqlAlchemyUserRepository(session).create(
            StoreUser(
                email="tenant@example.com",
                password_hash="hash",
                full_name="Tenant",
                role=Role.TENANT,
            )
        )
        await session.commit()
    room = await CreateRoomUseCase(RentalSqlAlchemyUnitOfWork(session_factory)).execute(
        manager,
        CreateRoom(
            name="A-101",
            building="A",
            floor=1,
            area_m2=Decimal("100.00"),
            monthly_rent=Decimal("15000.00"),
        ),
    )
    today = datetime.now(UTC).date()
    return CreateLease(
        number="LEASE-001",
        room_id=room.id,
        tenant_id=user.id,
        start_date=today,
        end_date=today + timedelta(days=30),
        monthly_rent=room.monthly_rent,
    )


async def test_lease_occupancy_and_tenant_isolation(
    session_factory: async_sessionmaker[AsyncSession], manager: Actor, lease_data: CreateLease
) -> None:
    lease = await CreateLeaseUseCase(RentalSqlAlchemyUnitOfWork(session_factory)).execute(
        manager, lease_data
    )
    tenant = Actor(lease_data.tenant_id, Role.TENANT)
    room = await GetRoomUseCase(RentalSqlAlchemyUnitOfWork(session_factory)).execute(
        tenant, lease.room_id
    )
    assert room.status == RoomStatus.OCCUPIED
    page = await GetRoomsUseCase(RentalSqlAlchemyUnitOfWork(session_factory)).execute(
        tenant, RoomFilter(status=RoomStatus.OCCUPIED)
    )
    assert [item.id for item in page.items] == [room.id]
    with pytest.raises(EntityNotFound):
        await GetRoomUseCase(RentalSqlAlchemyUnitOfWork(session_factory)).execute(
            Actor(uuid4(), Role.TENANT), room.id
        )
    await TerminateLeaseUseCase(RentalSqlAlchemyUnitOfWork(session_factory)).execute(
        manager, lease.id
    )
    free_room = await GetRoomUseCase(RentalSqlAlchemyUnitOfWork(session_factory)).execute(
        manager, room.id
    )
    assert free_room.status == RoomStatus.AVAILABLE
    with pytest.raises(EntityNotFound):
        await GetRoomUseCase(RentalSqlAlchemyUnitOfWork(session_factory)).execute(tenant, room.id)
    async with session_factory() as session:
        stored = await SqlAlchemyLeaseRepository(session).get_by_id(lease.id)
        assert stored is not None and stored.status == LeaseStatus.TERMINATED


async def test_database_rejects_overlaps_bypassing_use_case(
    session_factory: async_sessionmaker[AsyncSession], manager: Actor, lease_data: CreateLease
) -> None:
    await CreateLeaseUseCase(RentalSqlAlchemyUnitOfWork(session_factory)).execute(
        manager, lease_data
    )
    overlap = lease_data.model_copy(
        update={"number": "LEASE-002", "start_date": lease_data.end_date}
    )
    async with session_factory() as session:
        with pytest.raises(IntegrityError):
            await SqlAlchemyLeaseRepository(session).create(overlap)
        await session.rollback()
        assert await session.scalar(select(func.count()).select_from(LeaseOrm)) == 1


async def test_concurrent_overlapping_leases_have_one_winner(
    session_factory: async_sessionmaker[AsyncSession], manager: Actor, lease_data: CreateLease
) -> None:
    second_data = lease_data.model_copy(update={"number": "LEASE-002"})
    outcomes = await asyncio.gather(
        CreateLeaseUseCase(RentalSqlAlchemyUnitOfWork(session_factory)).execute(
            manager, lease_data
        ),
        CreateLeaseUseCase(RentalSqlAlchemyUnitOfWork(session_factory)).execute(
            manager, second_data
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(item, LeaseDTO) for item in outcomes) == 1
    assert sum(isinstance(item, Conflict) for item in outcomes) == 1
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(LeaseOrm)) == 1


async def test_room_and_lease_are_rolled_back_by_the_same_unit_of_work(
    session_factory: async_sessionmaker[AsyncSession], lease_data: CreateLease
) -> None:
    room_id = None
    lease_id = None
    with pytest.raises(RuntimeError, match="cancel operation"):
        async with RentalSqlAlchemyUnitOfWork(session_factory) as uow:
            room = await uow.rooms.create(
                CreateRoom(
                    name="ROLLBACK",
                    building="B",
                    floor=1,
                    area_m2=Decimal("50"),
                    monthly_rent=Decimal("100"),
                )
            )
            lease = await uow.leases.create(lease_data.model_copy(update={"room_id": room.id}))
            room_id, lease_id = room.id, lease.id
            raise RuntimeError("cancel operation")
    assert room_id is not None and lease_id is not None
    async with session_factory() as session:
        assert await SqlAlchemyRoomRepository(session).get_by_id(room_id) is None
        assert await SqlAlchemyLeaseRepository(session).get_by_id(lease_id) is None


async def test_maintenance_blocks_booking_and_future_booking_blocks_maintenance(
    session_factory: async_sessionmaker[AsyncSession], manager: Actor, lease_data: CreateLease
) -> None:
    await UpdateRoomUseCase(RentalSqlAlchemyUnitOfWork(session_factory)).execute(
        manager, lease_data.room_id, UpdateRoom(status=RoomStatus.MAINTENANCE)
    )
    with pytest.raises(BusinessRuleViolation):
        await CreateLeaseUseCase(RentalSqlAlchemyUnitOfWork(session_factory)).execute(
            manager, lease_data
        )
    await UpdateRoomUseCase(RentalSqlAlchemyUnitOfWork(session_factory)).execute(
        manager, lease_data.room_id, UpdateRoom(status=RoomStatus.AVAILABLE)
    )
    future = lease_data.model_copy(
        update={
            "start_date": lease_data.end_date + timedelta(days=1),
            "end_date": lease_data.end_date + timedelta(days=30),
        }
    )
    await CreateLeaseUseCase(RentalSqlAlchemyUnitOfWork(session_factory)).execute(manager, future)
    with pytest.raises(BusinessRuleViolation):
        await UpdateRoomUseCase(RentalSqlAlchemyUnitOfWork(session_factory)).execute(
            manager, lease_data.room_id, UpdateRoom(status=RoomStatus.MAINTENANCE)
        )
    async with session_factory() as session:
        repository = SqlAlchemyRoomRepository(session)
        room = await repository.get_by_id(lease_data.room_id)
        assert room is not None and room.status == RoomStatus.AVAILABLE
        assert not await repository.tenant_has_access(room.id, lease_data.tenant_id)
        assert await repository.tenant_has_access(room.id, lease_data.tenant_id, future.start_date)
