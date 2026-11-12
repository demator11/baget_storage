from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from baget_storage.domain.entities.user import Role
from baget_storage.infrastructure.postgres.rental_uow import RentalSqlAlchemyUnitOfWork
from baget_storage.infrastructure.postgres.repository.rental import SqlAlchemyRoomRepository
from baget_storage.infrastructure.postgres.repository.user import SqlAlchemyUserRepository
from baget_storage.schemas.rental import CreateRoom, RoomFilter, UpdateRoom
from baget_storage.schemas.user import StoreUser, UpdateProfile

pytestmark = pytest.mark.integration


def room_data(name: str = "A-101") -> CreateRoom:
    return CreateRoom(
        name=name,
        building="A",
        floor=1,
        area_m2=Decimal("100.25"),
        monthly_rent=Decimal("15000.00"),
        description="Loading area",
    )


async def test_create_read_generated_values_and_missing_records(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repository = SqlAlchemyRoomRepository(session)
        created = await repository.create(room_data())
        assert created.id is not None
        assert created.created_at.utcoffset().total_seconds() == 0
        assert created.updated_at == created.created_at
        assert await repository.get_by_id(created.id) == created
        assert await repository.get_by_id(uuid4()) is None
        assert await repository.update(uuid4(), UpdateRoom(description=None)) is None
        assert await repository.delete(uuid4()) is False
        await session.commit()
    async with session_factory() as session:
        assert await SqlAlchemyRoomRepository(session).get_by_id(created.id) == created


async def test_partial_update_explicit_null_and_timestamps(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        created = await SqlAlchemyRoomRepository(session).create(room_data())
        await session.commit()
    async with session_factory() as session:
        repository = SqlAlchemyRoomRepository(session)
        updated = await repository.update(created.id, UpdateRoom(description=None))
        assert updated is not None
        assert updated.description is None
        assert updated.name == created.name
        assert updated.area_m2 == created.area_m2
        assert updated.monthly_rent == created.monthly_rent
        assert updated.created_at == created.created_at
        assert updated.updated_at > created.updated_at
        await session.commit()
    async with session_factory() as session:
        repository = SqlAlchemyRoomRepository(session)
        unchanged = await repository.update(created.id, UpdateRoom())
        assert unchanged == updated
        assert await repository.delete(created.id) is True
        assert await repository.get_by_id(created.id) is None
        await session.commit()


async def test_two_repository_changes_rollback_in_one_transaction(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = None
    room_id = None
    with pytest.raises(RuntimeError, match="failed business operation"):
        async with session_factory() as session:
            async with session.begin():
                user = await SqlAlchemyUserRepository(session).create(
                    StoreUser(
                        email="rollback@example.com",
                        password_hash="hash",
                        full_name="Rollback Tenant",
                        role=Role.TENANT,
                    )
                )
                room = await SqlAlchemyRoomRepository(session).create(room_data())
                user_id, room_id = user.id, room.id
                raise RuntimeError("failed business operation")
    assert user_id is not None and room_id is not None
    async with session_factory() as session:
        assert await SqlAlchemyUserRepository(session).get_by_id(user_id) is None
        assert await SqlAlchemyRoomRepository(session).get_by_id(room_id) is None


async def test_user_repository_null_clear_and_unset_preservation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repository = SqlAlchemyUserRepository(session)
        user = await repository.create(
            StoreUser(
                email="profile@example.com",
                password_hash="hash",
                full_name="Tenant",
                role=Role.TENANT,
                company_name="Company",
                phone="+70000000000",
            )
        )
        updated = await repository.update(user.id, UpdateProfile(company_name=None))
        assert updated is not None
        assert updated.company_name is None
        assert updated.phone == user.phone
        assert updated.email == user.email


async def test_stable_pagination_search_and_empty_page(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with RentalSqlAlchemyUnitOfWork(session_factory) as uow:
        created = [await uow.rooms.create(room_data(name)) for name in ("A-03", "A-01", "A-02")]
        await uow.commit()
    async with session_factory() as session:
        repository = SqlAlchemyRoomRepository(session)
        first = await repository.list(RoomFilter(limit=2))
        second = await repository.list(RoomFilter(limit=2, offset=2))
        repeated = await repository.list(RoomFilter(limit=2))
        assert first == repeated
        assert [item.name for item in first.items + second.items] == ["A-01", "A-02", "A-03"]
        assert first.total == second.total == len(created)
        assert len({item.id for item in first.items + second.items}) == len(created)
        empty = await repository.list(RoomFilter(offset=100))
        assert empty.items == [] and empty.total == 3
        exact = await repository.list(RoomFilter(search="A-02"))
        assert [item.name for item in exact.items] == ["A-02"]
        literal_wildcard = await repository.list(RoomFilter(search="%"))
        assert literal_wildcard.total == 0
