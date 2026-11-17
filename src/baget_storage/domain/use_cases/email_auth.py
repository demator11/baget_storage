from datetime import timedelta

from baget_storage.core.exceptions import AuthenticationRequired
from baget_storage.domain.entities.auth import AuthPolicy
from baget_storage.domain.entities.user import Actor, validate_password
from baget_storage.domain.ports.auth import AuthClock, AuthUnitOfWork, PasswordHasher, TokenService
from baget_storage.schemas.user import EmailActionDTO, ResetPassword, UserCredentialsDTO, UserDTO


async def enqueue_email_action(
    uow: AuthUnitOfWork,
    user: UserDTO,
    purpose: str,
    tokens: TokenService,
    clock: AuthClock,
    policy: AuthPolicy,
) -> None:
    now = clock.now()
    latest = await uow.actions.latest_created_at(user.id, purpose)
    if latest is not None and now - latest < timedelta(seconds=60):
        return
    lifetime = (
        timedelta(hours=policy.verification_hours)
        if purpose == "verify_email"
        else timedelta(minutes=policy.password_reset_minutes)
    )
    expires_at = now + lifetime
    opaque = tokens.new_opaque()
    digest = tokens.hash_opaque(opaque)
    await uow.actions.invalidate(user.id, purpose, now)
    await uow.actions.create(user.id, purpose, digest, expires_at)
    path = "verify-email" if purpose == "verify_email" else "reset-password"
    await uow.outbox.enqueue(
        str(user.email),
        purpose,
        {
            "url": f"{policy.frontend_base_url.rstrip('/')}/{path}?token={opaque}",
            "full_name": user.full_name,
        },
        f"{purpose}:{digest}",
        expires_at=expires_at,
    )


async def valid_action(
    uow: AuthUnitOfWork, digest: str, purpose: str, clock: AuthClock
) -> tuple[EmailActionDTO, UserCredentialsDTO]:
    action = await uow.actions.get(digest)
    if action is None:
        raise AuthenticationRequired("Invalid or expired token.")
    user = await uow.users.get_credentials(action.user_id, lock=True)
    action = await uow.actions.get(digest, lock=True)
    if (
        user is None
        or not user.is_active
        or action is None
        or action.purpose != purpose
        or action.consumed_at is not None
        or action.expires_at <= clock.now()
    ):
        raise AuthenticationRequired("Invalid or expired token.")
    return action, user


class ResendVerificationUseCase:
    def __init__(
        self, uow: AuthUnitOfWork, tokens: TokenService, clock: AuthClock, policy: AuthPolicy
    ) -> None:
        self._uow, self._tokens, self._clock, self._policy = uow, tokens, clock, policy

    async def execute(self, actor: Actor) -> None:
        async with self._uow:
            user = await self._uow.users.get_credentials(actor.id, lock=True)
            if user is None or not user.is_active:
                raise AuthenticationRequired()
            if user.email_verified_at is not None:
                return
            await enqueue_email_action(
                self._uow, user, "verify_email", self._tokens, self._clock, self._policy
            )
            await self._uow.commit()


class ConfirmEmailUseCase:
    def __init__(self, uow: AuthUnitOfWork, tokens: TokenService, clock: AuthClock) -> None:
        self._uow, self._tokens, self._clock = uow, tokens, clock

    async def execute(self, token: str) -> None:
        async with self._uow:
            action, user = await valid_action(
                self._uow, self._tokens.hash_opaque(token), "verify_email", self._clock
            )
            await self._uow.users.verify_email(user.id, self._clock.now())
            await self._uow.actions.consume(action.id, self._clock.now())
            await self._uow.commit()


class RequestPasswordResetUseCase:
    def __init__(
        self, uow: AuthUnitOfWork, tokens: TokenService, clock: AuthClock, policy: AuthPolicy
    ) -> None:
        self._uow, self._tokens, self._clock, self._policy = uow, tokens, clock, policy

    async def execute(self, email: str) -> None:
        async with self._uow:
            candidate = await self._uow.users.get_by_email(email.casefold())
            if candidate is None:
                return
            user = await self._uow.users.get_credentials(candidate.id, lock=True)
            if user is None or not user.is_active:
                return
            await enqueue_email_action(
                self._uow, user, "password_reset", self._tokens, self._clock, self._policy
            )
            await self._uow.commit()


class CompletePasswordResetUseCase:
    def __init__(
        self, uow: AuthUnitOfWork, passwords: PasswordHasher, tokens: TokenService, clock: AuthClock
    ) -> None:
        self._uow, self._passwords, self._tokens, self._clock = uow, passwords, tokens, clock

    async def execute(self, data: ResetPassword) -> None:
        validate_password(data.new_password)
        password_hash = await self._passwords.hash(data.new_password)
        async with self._uow:
            action, user = await valid_action(
                self._uow, self._tokens.hash_opaque(data.token), "password_reset", self._clock
            )
            changed = await self._uow.users.change_password(
                user.id, password_hash, user.token_version
            )
            if not changed:
                raise AuthenticationRequired("Invalid or expired token.")
            await self._uow.sessions.revoke_all(user.id)
            await self._uow.actions.invalidate(user.id, "password_reset", self._clock.now())
            await self._uow.commit()
