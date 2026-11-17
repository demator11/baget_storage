from uuid import UUID

from baget_storage.core.exceptions import EntityNotFound
from baget_storage.domain.entities.user import Actor
from baget_storage.domain.ports.office import OfficeUnitOfWork
from baget_storage.schemas.office import NotificationCountsDTO, NotificationDTO


class GetNotificationUseCase:
    def __init__(self, uow: OfficeUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, actor: Actor, notification_id: UUID) -> NotificationDTO:
        async with self._uow:
            notification = await self._uow.notifications.get_by_id(actor.id, notification_id)
            if notification is None:
                raise EntityNotFound("Notification not found")
            return notification


class GetNotificationCountsUseCase:
    def __init__(self, uow: OfficeUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, actor: Actor) -> NotificationCountsDTO:
        async with self._uow:
            return await self._uow.notifications.counts(actor.id)
