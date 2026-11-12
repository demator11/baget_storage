from pydantic import BaseModel, ConfigDict


class ArchiveEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    filename: str
    content: bytes
