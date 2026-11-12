from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Query, Response

from baget_storage.api.auth import CurrentActor
from baget_storage.domain.use_cases.archive import DownloadDocumentsArchiveUseCase
from baget_storage.schemas.api.archive import ArchiveQuery
from baget_storage.schemas.office import DocumentFilter

router = APIRouter(route_class=DishkaRoute, tags=["Documents"])


@router.get("/documents/archive")
async def download_archive(
    filters: Annotated[ArchiveQuery, Query()],
    actor: CurrentActor,
    use_case: FromDishka[DownloadDocumentsArchiveUseCase],
) -> Response:
    result = await use_case.execute(actor, DocumentFilter(**filters.model_dump()))
    return Response(
        content=result,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="documents.zip"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
