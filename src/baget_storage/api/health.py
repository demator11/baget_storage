from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from baget_storage.domain.use_cases.health import CheckHealthUseCase

router = APIRouter(route_class=DishkaRoute, tags=["Health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(use_case: FromDishka[CheckHealthUseCase]) -> JSONResponse:
    healthy = await use_case.execute()
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "unavailable"},
    )
