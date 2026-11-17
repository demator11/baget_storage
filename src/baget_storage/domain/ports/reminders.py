from datetime import date
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from baget_storage.domain.ports.office import NotificationRepository
from baget_storage.schemas.reminders import InvoiceReminder, LeaseReminder, ReadingReminder


class ReminderRepository(Protocol):
    async def invoices_due(
        self, today: date, after: UUID | None = None, limit: int = 100
    ) -> list[InvoiceReminder]: ...

    async def leases_ending(
        self, today: date, after: UUID | None = None, limit: int = 100
    ) -> list[LeaseReminder]: ...

    async def missing_readings(
        self, period: date, after: tuple[UUID, UUID] | None = None, limit: int = 100
    ) -> list[ReadingReminder]: ...


class ReminderUnitOfWork(Protocol):
    reminders: ReminderRepository
    notifications: NotificationRepository

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...
