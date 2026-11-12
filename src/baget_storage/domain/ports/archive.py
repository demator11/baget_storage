from typing import Protocol

from baget_storage.schemas.archive import ArchiveEntry


class ArchiveBuilder(Protocol):
    async def build(self, entries: list[ArchiveEntry]) -> bytes: ...
