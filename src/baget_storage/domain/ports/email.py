from datetime import datetime
from typing import Protocol
from uuid import UUID

from baget_storage.schemas.email import EmailJob


class EmailOutbox(Protocol):
    async def enqueue(
        self,
        recipient: str,
        template: str,
        payload: dict[str, str],
        deduplication_key: str,
        *,
        expires_at: datetime | None = None,
    ) -> None: ...


class EmailSender(Protocol):
    async def send(self, job: EmailJob) -> None: ...


class EmailDeliveryStore(Protocol):
    async def claim(
        self, now: datetime, limit: int, lease_seconds: int, max_attempts: int
    ) -> list[EmailJob]: ...

    async def mark_sent(self, job_id: UUID, claim_id: UUID, now: datetime) -> None: ...

    async def mark_failed(
        self,
        job_id: UUID,
        claim_id: UUID,
        error_code: str,
        available_at: datetime,
        permanent: bool,
    ) -> None: ...


class WorkerClock(Protocol):
    def now(self) -> datetime: ...
