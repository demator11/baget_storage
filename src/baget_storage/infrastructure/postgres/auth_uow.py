from types import TracebackType
from typing import Self

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from baget_storage.core.exceptions import Conflict
from baget_storage.infrastructure.postgres.repository.user import SqlAlchemyUserRepository


class AuthSqlAlchemyUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self.users: SqlAlchemyUserRepository

    async def __aenter__(self) -> Self:
        if self._session is not None:
            raise RuntimeError("Unit of work is already active")
        self._session = self._session_factory()
        self.users = SqlAlchemyUserRepository(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._session is None:
            return
        session = self._session
        self._session = None
        try:
            await session.rollback()
        finally:
            await session.close()
        if isinstance(exc_value, IntegrityError):
            raise Conflict("Account data conflicts with an existing record.") from exc_value

    async def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("Unit of work is not active")
        await self._session.commit()
