from baget_storage.domain.ports.office import NotificationRepository
from baget_storage.schemas.office import CreateNotification, NotificationDTO


async def notify_user(
    repository: NotificationRepository, data: CreateNotification
) -> NotificationDTO | None:
    preferences = await repository.preferences(data.user_id)
    if not preferences.allows(data.category):
        return None
    return await repository.create(data)
