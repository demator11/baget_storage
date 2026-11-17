from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from baget_storage.infrastructure.postgres.repository.notification import (
    SqlAlchemyNotificationRepository,
)
from baget_storage.infrastructure.postgres.repository.reminders import SqlAlchemyReminderRepository


class ReminderSqlAlchemyUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self.notifications: SqlAlchemyNotificationRepository
        self.reminders: SqlAlchemyReminderRepository

    async def __aenter__(self) -> Self:
        if self._session is not None:
            raise RuntimeError("Reminder unit of work is already active")
        self._session = self._session_factory()
        self.notifications = SqlAlchemyNotificationRepository(self._session)
        self.reminders = SqlAlchemyReminderRepository(self._session)
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

    async def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("Reminder unit of work is not active")
        await self._session.commit()
