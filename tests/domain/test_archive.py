from datetime import UTC, date, datetime
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

import pytest

from baget_storage.core.exceptions import BusinessRuleViolation
from baget_storage.domain.entities.office import DocumentKind, DocumentStatus
from baget_storage.domain.entities.user import Actor, Role
from baget_storage.domain.use_cases.archive import DownloadDocumentsArchiveUseCase
from baget_storage.schemas.archive import ArchiveEntry
from baget_storage.schemas.office import DocumentDownload, DocumentDTO, DocumentFilter, Page


class FakeDocuments:
    def __init__(self) -> None:
        self.rows: list[tuple[DocumentDTO, UUID, bytes]] = []
        self.downloaded: list[UUID] = []
        self.last_filter: DocumentFilter | None = None
        self.last_tenant: UUID | None = None

    def add(self, tenant_id: UUID, *, size_bytes: int | None = None) -> DocumentDTO:
        content = b"%PDF-1.7\nexample"
        now = datetime.now(UTC)
        document = DocumentDTO(
            id=uuid4(),
            lease_id=uuid4(),
            title="Contract",
            kind=DocumentKind.CONTRACT,
            status=DocumentStatus.ACTIVE,
            issued_on=date(2026, 11, 1),
            filename="contract.pdf",
            size_bytes=len(content) if size_bytes is None else size_bytes,
            created_at=now,
            updated_at=now,
        )
        self.rows.append((document, tenant_id, content))
        return document

    async def list(self, filters: DocumentFilter, tenant_id: UUID | None) -> Page[DocumentDTO]:
        self.last_filter = filters
        self.last_tenant = tenant_id
        documents = [
            doc
            for doc, owner, _ in self.rows
            if (tenant_id is None or owner == tenant_id)
            and (filters.kind is None or doc.kind == filters.kind)
            and (filters.search is None or filters.search in doc.title)
        ]
        return Page(
            items=documents[filters.offset : filters.offset + filters.limit],
            total=len(documents),
            limit=filters.limit,
            offset=filters.offset,
        )

    async def download(self, document_id: UUID) -> DocumentDownload:
        self.downloaded.append(document_id)
        doc, _, content = next(row for row in self.rows if row[0].id == document_id)
        return DocumentDownload(filename=doc.filename, content=content)


class FakeArchiveUow:
    def __init__(self) -> None:
        self.documents = FakeDocuments()
        self.active = False

    async def __aenter__(self) -> Self:
        self.active = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.active = False


class FakeArchiveBuilder:
    def __init__(self, uow: FakeArchiveUow) -> None:
        self._uow = uow
        self.entries: list[ArchiveEntry] | None = None
        self.fail = False

    async def build(self, entries: list[ArchiveEntry]) -> bytes:
        assert not self._uow.active
        self.entries = entries
        if self.fail:
            raise RuntimeError("Archive creation failed")
        return b"archive-result"


async def test_archive_scopes_tenant_and_preserves_filters_with_bounded_fetch() -> None:
    tenant = Actor(uuid4(), Role.TENANT)
    uow = FakeArchiveUow()
    own = uow.documents.add(tenant.id)
    uow.documents.add(uuid4())
    builder = FakeArchiveBuilder(uow)
    filters = DocumentFilter(search="Contract", kind=DocumentKind.CONTRACT, limit=1, offset=50)
    result = await DownloadDocumentsArchiveUseCase(uow, builder).execute(tenant, filters)
    assert result == b"archive-result"
    assert uow.documents.last_tenant == tenant.id
    assert uow.documents.last_filter == filters.model_copy(update={"limit": 100, "offset": 0})
    assert uow.documents.downloaded == [own.id]
    assert builder.entries is not None
    assert [entry.filename for entry in builder.entries] == [f"{own.id}-contract.pdf"]
    assert builder.entries[0].content == b"%PDF-1.7\nexample"
    assert not uow.active


async def test_manager_archive_includes_tenants_and_disambiguates_filenames() -> None:
    uow = FakeArchiveUow()
    first, second = uow.documents.add(uuid4()), uow.documents.add(uuid4())
    builder = FakeArchiveBuilder(uow)
    await DownloadDocumentsArchiveUseCase(uow, builder).execute(
        Actor(uuid4(), Role.MANAGER), DocumentFilter()
    )
    assert uow.documents.last_tenant is None
    assert builder.entries is not None
    assert {entry.filename for entry in builder.entries} == {
        f"{first.id}-contract.pdf",
        f"{second.id}-contract.pdf",
    }


@pytest.mark.parametrize("count,size", [(101, 1), (6, 10 * 1024**2)])
async def test_oversized_archive_rejected_before_downloading(count: int, size: int) -> None:
    uow = FakeArchiveUow()
    for _ in range(count):
        uow.documents.add(uuid4(), size_bytes=size)
    builder = FakeArchiveBuilder(uow)
    with pytest.raises(BusinessRuleViolation, match="100 documents and 50 MiB"):
        await DownloadDocumentsArchiveUseCase(uow, builder).execute(
            Actor(uuid4(), Role.MANAGER), DocumentFilter()
        )
    assert not uow.documents.downloaded
    assert builder.entries is None
    assert not uow.active


@pytest.mark.parametrize("count,size", [(100, 1), (5, 10 * 1024**2)])
async def test_archive_accepts_exact_document_and_size_boundaries(count: int, size: int) -> None:
    uow = FakeArchiveUow()
    for _ in range(count):
        uow.documents.add(uuid4(), size_bytes=size)
    builder = FakeArchiveBuilder(uow)
    await DownloadDocumentsArchiveUseCase(uow, builder).execute(
        Actor(uuid4(), Role.MANAGER), DocumentFilter()
    )
    assert builder.entries is not None and len(builder.entries) == count
    assert len(uow.documents.downloaded) == count
    assert not uow.active


async def test_empty_archive_is_built_after_closing_the_uow() -> None:
    uow = FakeArchiveUow()
    builder = FakeArchiveBuilder(uow)
    result = await DownloadDocumentsArchiveUseCase(uow, builder).execute(
        Actor(uuid4(), Role.TENANT), DocumentFilter()
    )
    assert result == b"archive-result"
    assert builder.entries == []
    assert not uow.active


async def test_archive_failure_does_not_leave_transaction_open() -> None:
    uow = FakeArchiveUow()
    uow.documents.add(uuid4())
    builder = FakeArchiveBuilder(uow)
    builder.fail = True
    with pytest.raises(RuntimeError, match="Archive creation failed"):
        await DownloadDocumentsArchiveUseCase(uow, builder).execute(
            Actor(uuid4(), Role.MANAGER), DocumentFilter()
        )
    assert not uow.active
