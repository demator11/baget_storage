import asyncio
import io
import zipfile

from baget_storage.schemas.archive import ArchiveEntry


class ZipArchiveBuilder:
    async def build(self, entries: list[ArchiveEntry]) -> bytes:
        return await asyncio.to_thread(self._encode, entries)

    @staticmethod
    def _encode(entries: list[ArchiveEntry]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for entry in entries:
                archive.writestr(entry.filename, entry.content)
        return buffer.getvalue()
