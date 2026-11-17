from typing import Annotated
from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Query, Response, UploadFile

from baget_storage.api.auth import CurrentActor
from baget_storage.domain.use_cases.meter import (
    CorrectReadingUseCase,
    CreateMeterUseCase,
    GetMeterStatsUseCase,
    GetMetersUseCase,
    GetMeterUseCase,
    GetReadingJournalUseCase,
    GetReadingPeriodsUseCase,
    GetReadingsUseCase,
    GetReadingUseCase,
    ReviewReadingUseCase,
    SubmitReadingUseCase,
)
from baget_storage.domain.use_cases.meter_photo import (
    DownloadReadingPhotoUseCase,
    UploadReadingPhotoUseCase,
)
from baget_storage.schemas.api.meter import (
    CorrectReadingRequest,
    CreateMeterRequest,
    MeterPageResponse,
    MeterQuery,
    MeterResponse,
    MeterStatsResponse,
    ReadingPageResponse,
    ReadingPeriodPageResponse,
    ReadingPeriodQuery,
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
    ReadingPeriodFilter,
    ReviewReading,
    SubmitReading,
)

router = APIRouter(tags=["Meters"], route_class=DishkaRoute)


@router.get("/meters/stats", response_model=MeterStatsResponse)
async def meter_stats(
    query: Annotated[MeterQuery, Query()],
    actor: CurrentActor,
    use_case: FromDishka[GetMeterStatsUseCase],
) -> MeterStatsResponse:
    return MeterStatsResponse.model_validate(
        await use_case.execute(actor, MeterFilters(**query.model_dump()))
    )


@router.get("/reading-periods", response_model=ReadingPeriodPageResponse)
async def reading_periods(
    query: Annotated[ReadingPeriodQuery, Query()],
    actor: CurrentActor,
    use_case: FromDishka[GetReadingPeriodsUseCase],
) -> ReadingPeriodPageResponse:
    return ReadingPeriodPageResponse.model_validate(
        await use_case.execute(actor, ReadingPeriodFilter(**query.model_dump()))
    )


@router.get("/readings", response_model=ReadingPageResponse)
async def reading_journal(
    query: Annotated[ReadingQuery, Query()],
    actor: CurrentActor,
    use_case: FromDishka[GetReadingJournalUseCase],
) -> ReadingPageResponse:
    return ReadingPageResponse.model_validate(
        await use_case.execute(actor, ReadingFilters(**query.model_dump()))
    )


@router.get("/readings/{reading_id}", response_model=ReadingResponse)
async def get_reading(
    reading_id: UUID, actor: CurrentActor, use_case: FromDishka[GetReadingUseCase]
) -> ReadingResponse:
    return ReadingResponse.model_validate(await use_case.execute(actor, reading_id))


@router.put("/readings/{reading_id}/photo", response_model=ReadingResponse)
async def upload_reading_photo(
    reading_id: UUID,
    file: UploadFile,
    actor: CurrentActor,
    use_case: FromDishka[UploadReadingPhotoUseCase],
) -> ReadingResponse:
    try:
        content = await file.read(8 * 1024 * 1024 + 1)
    finally:
        await file.close()
    return ReadingResponse.model_validate(await use_case.execute(actor, reading_id, content))


@router.get("/readings/{reading_id}/photo", responses={200: {"content": {"image/jpeg": {}}}})
async def download_reading_photo(
    reading_id: UUID, actor: CurrentActor, use_case: FromDishka[DownloadReadingPhotoUseCase]
) -> Response:
    photo = await use_case.execute(actor, reading_id)
    return Response(
        content=photo.content,
        media_type=photo.content_type,
        headers={
            "Content-Disposition": f'inline; filename="reading-{reading_id}.jpg"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/meters/{meter_id}", response_model=MeterResponse)
async def get_meter(
    meter_id: UUID, actor: CurrentActor, use_case: FromDishka[GetMeterUseCase]
) -> MeterResponse:
    return MeterResponse.model_validate(await use_case.execute(actor, meter_id))


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
