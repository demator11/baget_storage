import io
import zipfile

from baget_storage.infrastructure.archive import ZipArchiveBuilder
from baget_storage.schemas.archive import ArchiveEntry


async def test_zip_roundtrip_preserves_filenames_and_binary_contents() -> None:
    entries = [
        ArchiveEntry(filename="first-договор.pdf", content=b"%PDF-1.7\n\x00\xff\x01"),
        ArchiveEntry(filename="second-invoice.pdf", content=b"%PDF-1.7\ninvoice"),
    ]
    result = await ZipArchiveBuilder().build(entries)
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        assert archive.namelist() == [entry.filename for entry in entries]
        assert archive.testzip() is None
        for entry in entries:
            assert archive.read(entry.filename) == entry.content


async def test_empty_zip_is_valid() -> None:
    result = await ZipArchiveBuilder().build([])
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        assert archive.namelist() == []
        assert archive.testzip() is None
