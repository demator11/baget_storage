from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter

from baget_storage.api.auth import CurrentActor
from baget_storage.domain.use_cases.notification_queries import (
    GetNotificationCountsUseCase,
    GetNotificationUseCase,
)
from baget_storage.schemas.api.notifications import NotificationCountsResponse
from baget_storage.schemas.api.office import NotificationResponse

router = APIRouter(prefix="/notifications", tags=["Notifications"], route_class=DishkaRoute)


@router.get("/summary", response_model=NotificationCountsResponse)
async def notification_counts(
    actor: CurrentActor, use_case: FromDishka[GetNotificationCountsUseCase]
) -> NotificationCountsResponse:
    result = await use_case.execute(actor)
    return NotificationCountsResponse(**result.model_dump())


@router.get("/{notification_id}", response_model=NotificationResponse)
async def notification_detail(
    notification_id: UUID,
    actor: CurrentActor,
    use_case: FromDishka[GetNotificationUseCase],
) -> NotificationResponse:
    result = await use_case.execute(actor, notification_id)
    return NotificationResponse(**result.model_dump())
