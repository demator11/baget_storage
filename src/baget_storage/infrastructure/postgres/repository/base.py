from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from baget_storage.infrastructure.postgres.base import Base


class BaseSqlAlchemyRepository[
    Model: Base,
    CreateDTO: BaseModel,
    UpdateDTO: BaseModel,
    ReadDTO: BaseModel,
]:
    model: type[Model]
    read_dto: type[ReadDTO]
    _protected_fields = frozenset({"id", "created_at", "updated_at"})

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _to_dto(self, row: Model) -> ReadDTO:
        return self.read_dto.model_validate(row, from_attributes=True)

    def _values(self, data: BaseModel, *, partial: bool = False) -> dict[str, object]:
        values = data.model_dump(exclude_unset=partial)
        if self._protected_fields.intersection(values):
            raise ValueError("Generated fields cannot be set through a repository write DTO")
        return values

    async def create(self, data: CreateDTO) -> ReadDTO:
        row = self.model(**self._values(data))
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return self._to_dto(row)

    async def get_by_id(self, entity_id: UUID) -> ReadDTO | None:
        row = await self._session.get(self.model, entity_id)
        return self._to_dto(row) if row is not None else None

    async def update(self, entity_id: UUID, data: UpdateDTO) -> ReadDTO | None:
        values = self._values(data, partial=True)
        row = await self._session.get(self.model, entity_id)
        if row is None:
            return None
        if not values:
            return self._to_dto(row)
        for field, value in values.items():
            setattr(row, field, value)
        await self._session.flush()
        await self._session.refresh(row)
        return self._to_dto(row)

    async def delete(self, entity_id: UUID) -> bool:
        row = await self._session.get(self.model, entity_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True
