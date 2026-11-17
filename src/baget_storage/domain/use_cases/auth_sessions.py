from baget_storage.core.exceptions import AuthenticationRequired
from baget_storage.domain.entities.auth import AuthPolicy
from baget_storage.domain.entities.user import Actor, Role
from baget_storage.domain.ports.auth import AuthClock, AuthUnitOfWork, TokenService
from baget_storage.schemas.user import SessionDTO, SessionTokens, UserCredentialsDTO, UserDTO


async def user_profile(uow: AuthUnitOfWork, user: UserDTO) -> UserDTO:
    profile = UserDTO(**user.model_dump())
    if user.role is Role.MANAGER:
        profile.accessible_buildings = await uow.users.accessible_buildings()
    return profile


async def build_session_tokens(
    uow: AuthUnitOfWork,
    tokens: TokenService,
    clock: AuthClock,
    user: UserCredentialsDTO,
    session: SessionDTO,
) -> SessionTokens:
    opaque = tokens.new_opaque()
    await uow.sessions.add_refresh(session.id, tokens.hash_opaque(opaque), session.expires_at)
    access = tokens.issue(user.id, user.token_version, session.id)
    return SessionTokens(
        **access.model_dump(),
        refresh_token=opaque,
        refresh_expires_in=max(0, int((session.expires_at - clock.now()).total_seconds())),
        session_id=session.id,
        user=await user_profile(uow, user),
    )


class RefreshSessionUseCase:
    def __init__(
        self, uow: AuthUnitOfWork, tokens: TokenService, clock: AuthClock, policy: AuthPolicy
    ) -> None:
        self._uow = uow
        self._tokens = tokens
        self._clock = clock
        self._policy = policy

    async def execute(self, refresh_token: str) -> SessionTokens:
        digest = self._tokens.hash_opaque(refresh_token)
        async with self._uow:
            token = await self._uow.sessions.get_refresh(digest)
            if token is None:
                raise AuthenticationRequired("Invalid or expired session.")
            session = await self._uow.sessions.get(token.session_id)
            if session is None:
                raise AuthenticationRequired("Invalid or expired session.")
            user = await self._uow.users.get_credentials(session.user_id, lock=True)
            session = await self._uow.sessions.get(token.session_id, lock=True)
            token = await self._uow.sessions.get_refresh(digest, lock=True)
            now = self._clock.now()
            if (
                user is None
                or session is None
                or token is None
                or not user.is_active
                or session.revoked_at is not None
                or session.expires_at <= now
                or token.expires_at <= now
                or user.token_version != session.token_version
            ):
                raise AuthenticationRequired("Invalid or expired session.")
            if token.consumed_at is not None:
                await self._uow.sessions.revoke(session.id, user.id)
                await self._uow.commit()
                raise AuthenticationRequired("Refresh token was already used. Sign in again.")
            await self._uow.sessions.consume_refresh(token.id, now)
            result = await build_session_tokens(self._uow, self._tokens, self._clock, user, session)
            await self._uow.commit()
            return result


class GetSessionsUseCase:
    def __init__(self, uow: AuthUnitOfWork, clock: AuthClock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(self, actor: Actor) -> list[SessionDTO]:
        async with self._uow:
            return await self._uow.sessions.list_active(actor.id, self._clock.now())
