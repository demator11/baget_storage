from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from baget_storage.infrastructure.postgres.models.auth import (
    AuthSessionOrm,
    EmailActionTokenOrm,
    RefreshTokenOrm,
)
from baget_storage.schemas.user import EmailActionDTO, RefreshTokenDTO, SessionDTO, StoreSession


class SqlAlchemySessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, data: StoreSession) -> SessionDTO:
        row = AuthSessionOrm(**data.model_dump())
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return SessionDTO.model_validate(row)

    async def get(self, session_id: UUID, *, lock: bool = False) -> SessionDTO | None:
        query = (
            select(AuthSessionOrm)
            .where(AuthSessionOrm.id == session_id)
            .execution_options(populate_existing=True)
        )
        if lock:
            query = query.with_for_update()
        row = await self._session.scalar(query)
        return SessionDTO.model_validate(row) if row else None

    async def add_refresh(self, session_id: UUID, token_hash: str, expires_at: datetime) -> None:
        self._session.add(
            RefreshTokenOrm(session_id=session_id, token_hash=token_hash, expires_at=expires_at)
        )
        await self._session.flush()

    async def get_refresh(self, token_hash: str, *, lock: bool = False) -> RefreshTokenDTO | None:
        query = (
            select(RefreshTokenOrm)
            .where(RefreshTokenOrm.token_hash == token_hash)
            .execution_options(populate_existing=True)
        )
        if lock:
            query = query.with_for_update()
        row = await self._session.scalar(query)
        return RefreshTokenDTO.model_validate(row) if row else None

    async def consume_refresh(self, token_id: UUID, now: datetime) -> None:
        session_id = await self._session.scalar(
            update(RefreshTokenOrm)
            .where(RefreshTokenOrm.id == token_id)
            .values(consumed_at=now)
            .returning(RefreshTokenOrm.session_id)
        )
        await self._session.execute(
            update(AuthSessionOrm).where(AuthSessionOrm.id == session_id).values(last_used_at=now)
        )

    async def revoke(self, session_id: UUID, user_id: UUID) -> bool:
        result = await self._session.scalar(
            update(AuthSessionOrm)
            .where(AuthSessionOrm.id == session_id, AuthSessionOrm.user_id == user_id)
            .values(revoked_at=func.coalesce(AuthSessionOrm.revoked_at, func.now()))
            .returning(AuthSessionOrm.id)
        )
        return result is not None

    async def revoke_all(self, user_id: UUID) -> None:
        await self._session.execute(
            update(AuthSessionOrm)
            .where(AuthSessionOrm.user_id == user_id, AuthSessionOrm.revoked_at.is_(None))
            .values(revoked_at=func.now())
        )

    async def list_active(self, user_id: UUID, now: datetime) -> list[SessionDTO]:
        rows = await self._session.scalars(
            select(AuthSessionOrm)
            .where(
                AuthSessionOrm.user_id == user_id,
                AuthSessionOrm.revoked_at.is_(None),
                AuthSessionOrm.expires_at > now,
            )
            .order_by(AuthSessionOrm.created_at.desc(), AuthSessionOrm.id)
            .limit(100)
        )
        return [SessionDTO.model_validate(row) for row in rows]


class SqlAlchemyEmailActionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, user_id: UUID, purpose: str, token_hash: str, expires_at: datetime
    ) -> None:
        self._session.add(
            EmailActionTokenOrm(
                user_id=user_id, purpose=purpose, token_hash=token_hash, expires_at=expires_at
            )
        )
        await self._session.flush()

    async def get(self, token_hash: str, *, lock: bool = False) -> EmailActionDTO | None:
        query = (
            select(EmailActionTokenOrm)
            .where(EmailActionTokenOrm.token_hash == token_hash)
            .execution_options(populate_existing=True)
        )
        if lock:
            query = query.with_for_update()
        row = await self._session.scalar(query)
        return EmailActionDTO.model_validate(row) if row else None

    async def consume(self, token_id: UUID, now: datetime) -> None:
        await self._session.execute(
            update(EmailActionTokenOrm)
            .where(EmailActionTokenOrm.id == token_id)
            .values(consumed_at=now)
        )

    async def invalidate(self, user_id: UUID, purpose: str, now: datetime) -> None:
        await self._session.execute(
            update(EmailActionTokenOrm)
            .where(
                EmailActionTokenOrm.user_id == user_id,
                EmailActionTokenOrm.purpose == purpose,
                EmailActionTokenOrm.consumed_at.is_(None),
            )
            .values(consumed_at=now)
        )

    async def latest_created_at(self, user_id: UUID, purpose: str) -> datetime | None:
        return await self._session.scalar(
            select(func.max(EmailActionTokenOrm.created_at)).where(
                EmailActionTokenOrm.user_id == user_id,
                EmailActionTokenOrm.purpose == purpose,
            )
        )
