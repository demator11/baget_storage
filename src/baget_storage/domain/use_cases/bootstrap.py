from baget_storage.core.exceptions import Conflict, EntityNotFound
from baget_storage.domain.entities.user import Role, validate_password, validate_profile
from baget_storage.domain.ports.auth import AuthClock, AuthUnitOfWork, PasswordHasher
from baget_storage.schemas.bootstrap import ResetPassword
from baget_storage.schemas.user import CreateUser, StoreUser, UserDTO


class CreateManagerUseCase:
    def __init__(self, uow: AuthUnitOfWork, passwords: PasswordHasher) -> None:
        self._uow = uow
        self._passwords = passwords

    async def execute(self, data: CreateUser) -> UserDTO:
        validate_password(data.password)
        validate_profile(data.full_name)
        hashed = await self._passwords.hash(data.password)
        async with self._uow as uow:
            if await uow.users.get_by_email(str(data.email).casefold()) is not None:
                raise Conflict("An account with this email already exists")
            user = await uow.users.create(
                StoreUser(
                    email=str(data.email).casefold(),
                    password_hash=hashed,
                    full_name=data.full_name.strip(),
                    role=Role.MANAGER,
                    company_name=data.company_name,
                    phone=data.phone,
                    tax_id=data.tax_id,
                )
            )
            await uow.commit()
            return user


class ResetPasswordUseCase:
    def __init__(self, uow: AuthUnitOfWork, passwords: PasswordHasher, clock: AuthClock) -> None:
        self._uow = uow
        self._passwords = passwords
        self._clock = clock

    async def execute(self, data: ResetPassword) -> None:
        validate_password(data.password)
        hashed = await self._passwords.hash(data.password)
        async with self._uow as uow:
            user = await uow.users.get_by_email(str(data.email).casefold())
            if user is None:
                raise EntityNotFound("User not found")
            if not await uow.users.change_password(user.id, hashed, user.token_version):
                raise Conflict("Account changed; retry password reset")
            await uow.sessions.revoke_all(user.id)
            await uow.actions.invalidate(user.id, "password_reset", self._clock.now())
            await uow.commit()
