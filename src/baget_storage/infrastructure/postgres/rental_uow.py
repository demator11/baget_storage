from types import TracebackType
from typing import Self

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from baget_storage.core.exceptions import Conflict
from baget_storage.infrastructure.postgres.repository.rental import (
    SqlAlchemyLeaseRepository,
    SqlAlchemyRoomRepository,
)


class RentalSqlAlchemyUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> Self:
        self._session = self._session_factory()
        self.rooms = SqlAlchemyRoomRepository(self._session)
        self.leases = SqlAlchemyLeaseRepository(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._session is not None:
            try:
                await self._session.rollback()
            finally:
                await self._session.close()
                self._session = None
        if isinstance(exc, IntegrityError):
            raise Conflict("The rental operation conflicts with existing records.") from exc

    async def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("Unit of work has not been entered")
        await self._session.commit()
