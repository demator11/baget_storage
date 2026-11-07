from typing import Annotated
from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Query

from baget_storage.api.auth import CurrentActor
from baget_storage.domain.use_cases.meter import (
    CorrectReadingUseCase,
    CreateMeterUseCase,
    GetMetersUseCase,
    GetReadingsUseCase,
    ReviewReadingUseCase,
    SubmitReadingUseCase,
)
from baget_storage.schemas.api.meter import (
    CorrectReadingRequest,
    CreateMeterRequest,
    MeterPageResponse,
    MeterQuery,
    MeterResponse,
    ReadingPageResponse,
    ReadingQuery,
    ReadingResponse,
    ReviewReadingRequest,
    SubmitReadingRequest,
)
from baget_storage.schemas.meter import (
    CorrectReading,
    CreateMeter,
    MeterFilters,
    ReadingFilters,
    ReviewReading,
    SubmitReading,
)

router = APIRouter(tags=["Meters"], route_class=DishkaRoute)


@router.post("/meters", status_code=201, response_model=MeterResponse)
async def create_meter(
    data: CreateMeterRequest,
    actor: CurrentActor,
    use_case: FromDishka[CreateMeterUseCase],
) -> MeterResponse:
    result = await use_case.execute(actor, CreateMeter(**data.model_dump()))
    return MeterResponse.model_validate(result)


@router.get("/meters", response_model=MeterPageResponse)
async def list_meters(
    query: Annotated[MeterQuery, Query()],
    actor: CurrentActor,
    use_case: FromDishka[GetMetersUseCase],
) -> MeterPageResponse:
    result = await use_case.execute(actor, MeterFilters(**query.model_dump()))
    return MeterPageResponse.model_validate(result)


@router.post("/meters/{meter_id}/readings", status_code=201, response_model=ReadingResponse)
async def submit_reading(
    meter_id: UUID,
    data: SubmitReadingRequest,
    actor: CurrentActor,
    use_case: FromDishka[SubmitReadingUseCase],
) -> ReadingResponse:
    result = await use_case.execute(actor, meter_id, SubmitReading(**data.model_dump()))
    return ReadingResponse.model_validate(result)


@router.get("/meters/{meter_id}/readings", response_model=ReadingPageResponse)
async def list_readings(
    meter_id: UUID,
    query: Annotated[ReadingQuery, Query()],
    actor: CurrentActor,
    use_case: FromDishka[GetReadingsUseCase],
) -> ReadingPageResponse:
    result = await use_case.execute(actor, meter_id, ReadingFilters(**query.model_dump()))
    return ReadingPageResponse.model_validate(result)


@router.post("/readings/{reading_id}/review", response_model=ReadingResponse)
async def review_reading(
    reading_id: UUID,
    data: ReviewReadingRequest,
    actor: CurrentActor,
    use_case: FromDishka[ReviewReadingUseCase],
) -> ReadingResponse:
    result = await use_case.execute(actor, reading_id, ReviewReading(**data.model_dump()))
    return ReadingResponse.model_validate(result)


@router.post("/readings/{reading_id}/correction", response_model=ReadingResponse)
async def correct_reading(
    reading_id: UUID,
    data: CorrectReadingRequest,
    actor: CurrentActor,
    use_case: FromDishka[CorrectReadingUseCase],
) -> ReadingResponse:
    result = await use_case.execute(actor, reading_id, CorrectReading(**data.model_dump()))
    return ReadingResponse.model_validate(result)
