from uuid import UUID

from baget_storage.core.exceptions import AccessDenied, Conflict, EntityNotFound
from baget_storage.domain.entities.meter import ReadingStatus
from baget_storage.domain.entities.user import Actor, Role
from baget_storage.domain.ports.meter import MeterClock, MeterPhotoProcessor, MeterUnitOfWork
from baget_storage.schemas.meter import ReadingDTO, ReadingPhotoDTO


class UploadReadingPhotoUseCase:
    def __init__(
        self, uow: MeterUnitOfWork, processor: MeterPhotoProcessor, clock: MeterClock
    ) -> None:
        self._uow = uow
        self._processor = processor
        self._clock = clock

    async def execute(self, actor: Actor, reading_id: UUID, content: bytes) -> ReadingDTO:
        if actor.role is not Role.TENANT:
            raise AccessDenied("Only the submitting tenant can attach a photo.")
        photo = await self._processor.process(content)
        async with self._uow:
            reading = await self._uow.meters.get_reading(reading_id)
            if reading is None:
                raise EntityNotFound("Reading was not found.")
            if reading.submitted_by != actor.id:
                raise AccessDenied()
            meter = await self._uow.meters.get_by_id(reading.meter_id, lock=True)
            if meter is None:
                raise EntityNotFound("Meter was not found.")
            if not await self._uow.meters.tenant_has_access(
                meter.room_id, actor.id, self._clock.today()
            ):
                raise AccessDenied()
            latest = await self._uow.meters.latest_reading(meter.id)
            if latest is None or latest.id != reading_id or latest.status is ReadingStatus.ACCEPTED:
                raise Conflict("Only the latest unaccepted reading photo can be changed.")
            result = await self._uow.meters.save_photo(reading_id, photo)
            await self._uow.commit()
            return result


class DownloadReadingPhotoUseCase:
    def __init__(self, uow: MeterUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, actor: Actor, reading_id: UUID) -> ReadingPhotoDTO:
        async with self._uow:
            reading = await self._uow.meters.get_reading(reading_id)
            if reading is None:
                raise EntityNotFound("Reading was not found.")
            if actor.role is not Role.MANAGER and reading.submitted_by != actor.id:
                raise AccessDenied()
            photo = await self._uow.meters.get_photo(reading_id)
            if photo is None:
                raise EntityNotFound("Reading photo was not found.")
            return photo
