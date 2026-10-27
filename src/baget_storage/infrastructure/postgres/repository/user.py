from uuid import UUID

from sqlalchemy import func, select, update

from baget_storage.domain.entities.user import Role
from baget_storage.infrastructure.postgres.models.user import UserOrm
from baget_storage.infrastructure.postgres.repository.base import BaseSqlAlchemyRepository
from baget_storage.schemas.user import (
    StoreUser,
    UpdateProfile,
    UserCredentialsDTO,
    UserDTO,
    UserPage,
)


class SqlAlchemyUserRepository(
    BaseSqlAlchemyRepository[UserOrm, StoreUser, UpdateProfile, UserDTO]
):
    model = UserOrm
    read_dto = UserDTO

    async def get_credentials(self, entity_id: UUID) -> UserCredentialsDTO | None:
        row = await self._session.get(UserOrm, entity_id)
        return UserCredentialsDTO.model_validate(row) if row is not None else None

    async def get_by_email(self, email: str) -> UserCredentialsDTO | None:
        row = await self._session.scalar(select(UserOrm).where(UserOrm.email == email.casefold()))
        return UserCredentialsDTO.model_validate(row) if row is not None else None

    async def change_password(
        self, entity_id: UUID, password_hash: str, expected_version: int
    ) -> bool:
        statement = (
            update(UserOrm)
            .where(
                UserOrm.id == entity_id,
                UserOrm.token_version == expected_version,
                UserOrm.is_active.is_(True),
            )
            .values(password_hash=password_hash, token_version=UserOrm.token_version + 1)
            .returning(UserOrm.id)
        )
        return await self._session.scalar(statement) is not None

    async def revoke_tokens(self, entity_id: UUID) -> bool:
        statement = (
            update(UserOrm)
            .where(UserOrm.id == entity_id, UserOrm.is_active.is_(True))
            .values(token_version=UserOrm.token_version + 1)
            .returning(UserOrm.id)
        )
        return await self._session.scalar(statement) is not None

    async def list_tenants(self, limit: int, offset: int) -> UserPage:
        condition = UserOrm.role == Role.TENANT
        total = await self._session.scalar(
            select(func.count()).select_from(UserOrm).where(condition)
        )
        rows = await self._session.scalars(
            select(UserOrm)
            .where(condition)
            .order_by(UserOrm.full_name, UserOrm.id)
            .limit(limit)
            .offset(offset)
        )
        return UserPage(
            items=[self._to_dto(row) for row in rows],
            total=total or 0,
            limit=limit,
            offset=offset,
        )
