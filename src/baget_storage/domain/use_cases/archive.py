from baget_storage.core.exceptions import BusinessRuleViolation
from baget_storage.domain.entities.user import Actor, Role
from baget_storage.domain.ports.archive import ArchiveBuilder
from baget_storage.domain.ports.office import OfficeUnitOfWork
from baget_storage.schemas.archive import ArchiveEntry
from baget_storage.schemas.office import DocumentFilter


class DownloadDocumentsArchiveUseCase:
    def __init__(self, uow: OfficeUnitOfWork, archives: ArchiveBuilder) -> None:
        self._uow = uow
        self._archives = archives

    async def execute(self, actor: Actor, filters: DocumentFilter) -> bytes:
        tenant_id = actor.id if actor.role == Role.TENANT else None
        bounded = filters.model_copy(update={"limit": 100, "offset": 0})
        entries = []
        async with self._uow as uow:
            documents = await uow.documents.list(bounded, tenant_id)
            if (
                documents.total > 100
                or sum(doc.size_bytes for doc in documents.items) > 50 * 1024**2
            ):
                raise BusinessRuleViolation("Narrow the filter to at most 100 documents and 50 MiB")
            for document in documents.items:
                download = await uow.documents.download(document.id)
                entries.append(
                    ArchiveEntry(
                        filename=f"{document.id}-{download.filename}", content=download.content
                    )
                )
        return await self._archives.build(entries)
