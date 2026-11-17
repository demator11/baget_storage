from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import EmailStr, TypeAdapter
from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from baget_storage.infrastructure.postgres.models.email import EmailOutboxOrm
from baget_storage.schemas.email import EmailJob


class SqlAlchemyEmailOutbox:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(
        self,
        recipient: str,
        template: str,
        payload: dict[str, str],
        deduplication_key: str,
        *,
        expires_at: datetime | None = None,
    ) -> None:
        if template not in {"notification", "verify_email", "password_reset"}:
            raise ValueError("Unknown email template")
        if not deduplication_key or len(deduplication_key) > 200:
            raise ValueError("Invalid email deduplication key")
        recipient = str(TypeAdapter(EmailStr).validate_python(recipient))
        payload = TypeAdapter(dict[str, str]).validate_python(payload)
        if expires_at is None:
            hours = {"notification": 168, "verify_email": 24, "password_reset": 1}[template]
            expires_at = datetime.now(UTC) + timedelta(hours=hours)
        if expires_at.tzinfo is None:
            raise ValueError("Email expiry must include a timezone")
        await self._session.execute(
            insert(EmailOutboxOrm)
            .values(
                recipient=recipient,
                template=template,
                payload=payload,
                deduplication_key=deduplication_key,
                expires_at=expires_at,
            )
            .on_conflict_do_nothing(index_elements=[EmailOutboxOrm.deduplication_key])
        )


class SqlAlchemyEmailDeliveryStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def claim(
        self, now: datetime, limit: int, lease_seconds: int, max_attempts: int
    ) -> list[EmailJob]:
        if not 1 <= limit <= 100 or lease_seconds < 1 or max_attempts < 1:
            raise ValueError("Invalid email claim limits")
        eligible = or_(
            EmailOutboxOrm.status == "pending",
            and_(EmailOutboxOrm.status == "processing", EmailOutboxOrm.locked_until <= now),
        )
        async with self._session_factory.begin() as session:
            await session.execute(
                update(EmailOutboxOrm)
                .where(eligible, EmailOutboxOrm.expires_at <= now)
                .values(status="expired", payload={}, claim_id=None, locked_until=None)
            )
            await session.execute(
                update(EmailOutboxOrm)
                .where(eligible, EmailOutboxOrm.attempts >= max_attempts)
                .values(
                    status="failed",
                    payload={},
                    claim_id=None,
                    locked_until=None,
                    last_error="attempts_exhausted",
                )
            )
            rows = list(
                await session.scalars(
                    select(EmailOutboxOrm)
                    .where(
                        eligible,
                        EmailOutboxOrm.available_at <= now,
                        EmailOutboxOrm.attempts < max_attempts,
                        or_(EmailOutboxOrm.expires_at.is_(None), EmailOutboxOrm.expires_at > now),
                    )
                    .order_by(EmailOutboxOrm.available_at, EmailOutboxOrm.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            jobs = []
            for row in rows:
                row.status = "processing"
                row.attempts += 1
                row.claim_id = uuid4()
                row.locked_until = now + timedelta(seconds=lease_seconds)
                jobs.append(EmailJob.model_validate(row))
            return jobs

    async def mark_sent(self, job_id: UUID, claim_id: UUID, now: datetime) -> None:
        async with self._session_factory.begin() as session:
            await session.execute(
                update(EmailOutboxOrm)
                .where(
                    EmailOutboxOrm.id == job_id,
                    EmailOutboxOrm.claim_id == claim_id,
                    EmailOutboxOrm.status == "processing",
                )
                .values(
                    status="sent",
                    sent_at=now,
                    payload={},
                    claim_id=None,
                    locked_until=None,
                    last_error=None,
                )
            )

    async def mark_failed(
        self,
        job_id: UUID,
        claim_id: UUID,
        error_code: str,
        available_at: datetime,
        permanent: bool,
    ) -> None:
        values = {
            "status": "failed" if permanent else "pending",
            "available_at": available_at,
            "last_error": error_code[:100],
            "claim_id": None,
            "locked_until": None,
        }
        if permanent:
            values["payload"] = {}
        async with self._session_factory.begin() as session:
            await session.execute(
                update(EmailOutboxOrm)
                .where(
                    EmailOutboxOrm.id == job_id,
                    EmailOutboxOrm.claim_id == claim_id,
                    EmailOutboxOrm.status == "processing",
                )
                .values(**values)
            )
