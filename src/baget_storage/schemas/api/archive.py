from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from baget_storage.domain.entities.office import DocumentKind


class ArchiveQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    search: str | None = Field(default=None, max_length=200)
    room_id: UUID | None = None
    kind: DocumentKind | None = None
