from pydantic import BaseModel

from baget_storage.domain.entities.office import NotificationCategory


class NotificationCountsResponse(BaseModel):
    total: int
    unread: int
    categories: dict[NotificationCategory, int]
    unread_by_category: dict[NotificationCategory, int]
