from uuid import UUID

from baget_storage.domain.entities.office import NotificationCategory, NotificationResource
from baget_storage.domain.ports.office import NotificationRepository
from baget_storage.schemas.office import CreateNotification, NotificationDTO


async def notify_user(
    repository: NotificationRepository, data: CreateNotification
) -> NotificationDTO:
    notification = await repository.create(data)
    preferences = await repository.preferences(data.user_id)
    if preferences.allows(data.category):
        recipient = await repository.verified_email(data.user_id)
        if recipient is not None:
            await repository.enqueue_email(notification, recipient)
    return notification


async def notify_managers(
    repository: NotificationRepository,
    *,
    title: str,
    body: str,
    category: NotificationCategory,
    resource_type: NotificationResource | None = None,
    resource_id: UUID | None = None,
    event_key: str | None = None,
) -> int:
    after = None
    count = 0
    while managers := await repository.manager_ids(after=after, limit=100):
        for manager_id in managers:
            await notify_user(
                repository,
                CreateNotification(
                    user_id=manager_id,
                    title=title,
                    body=body,
                    category=category,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    deduplication_key=f"{event_key}:{manager_id}" if event_key else None,
                ),
            )
            count += 1
        after = managers[-1]
    return count
