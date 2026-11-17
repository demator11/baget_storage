from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka, inject
from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from baget_storage.core.exceptions import AuthenticationRequired
from baget_storage.domain.entities.user import Actor
from baget_storage.domain.ports.auth import TokenService
from baget_storage.domain.use_cases.auth import (
    AuthenticateUserUseCase,
    ChangePasswordUseCase,
    CreateUserUseCase,
    GetProfileUseCase,
    ListUsersUseCase,
    LoginUseCase,
    LogoutUseCase,
    UpdateProfileUseCase,
)
from baget_storage.domain.use_cases.auth_sessions import GetSessionsUseCase, RefreshSessionUseCase
from baget_storage.domain.use_cases.email_auth import (
    CompletePasswordResetUseCase,
    ConfirmEmailUseCase,
    RequestPasswordResetUseCase,
    ResendVerificationUseCase,
)
from baget_storage.schemas.api.auth import (
    AccessTokenResponse,
    ChangePasswordRequest,
    CompletePasswordResetRequest,
    CreateUserRequest,
    EmailTokenRequest,
    LoginRequest,
    PasswordResetRequest,
    RefreshRequest,
    SessionResponse,
    UpdateProfileRequest,
    UserPageResponse,
    UserResponse,
)
from baget_storage.schemas.user import (
    ChangePassword,
    CreateUser,
    Login,
    ResetPassword,
    UpdateProfile,
)

router = APIRouter(route_class=DishkaRoute, tags=["Authentication"])
bearer = HTTPBearer(auto_error=False)


@inject
async def get_actor(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    tokens: FromDishka[TokenService],
    authenticate: FromDishka[AuthenticateUserUseCase],
) -> Actor:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationRequired()
    claims = tokens.decode(credentials.credentials)
    return await authenticate.execute(claims)


CurrentActor = Annotated[Actor, Depends(get_actor)]


@router.post("/auth/login", response_model=AccessTokenResponse)
async def login(
    request: LoginRequest, response: Response, use_case: FromDishka[LoginUseCase]
) -> AccessTokenResponse:
    response.headers["Cache-Control"] = "no-store"
    result = await use_case.execute(Login(**request.model_dump()))
    return AccessTokenResponse(**result.model_dump())


@router.get("/users/me", response_model=UserResponse)
async def get_profile(actor: CurrentActor, use_case: FromDishka[GetProfileUseCase]) -> UserResponse:
    result = await use_case.execute(actor)
    return UserResponse(**result.model_dump())


@router.patch("/users/me", response_model=UserResponse)
async def update_profile(
    request: UpdateProfileRequest, actor: CurrentActor, use_case: FromDishka[UpdateProfileUseCase]
) -> UserResponse:
    result = await use_case.execute(actor, UpdateProfile(**request.model_dump(exclude_unset=True)))
    return UserResponse(**result.model_dump())


@router.post("/auth/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    request: ChangePasswordRequest, actor: CurrentActor, use_case: FromDishka[ChangePasswordUseCase]
) -> Response:
    await use_case.execute(actor, ChangePassword(**request.model_dump()))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out of the current session",
)
async def logout(actor: CurrentActor, use_case: FromDishka[LogoutUseCase]) -> Response:
    await use_case.execute(actor)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    request: CreateUserRequest, actor: CurrentActor, use_case: FromDishka[CreateUserUseCase]
) -> UserResponse:
    result = await use_case.execute(actor, CreateUser(**request.model_dump()))
    return UserResponse(**result.model_dump())


@router.get("/users", response_model=UserPageResponse)
async def list_users(
    actor: CurrentActor,
    use_case: FromDishka[ListUsersUseCase],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserPageResponse:
    result = await use_case.execute(actor, limit, offset)
    return UserPageResponse(**result.model_dump())


@router.post("/auth/refresh", response_model=AccessTokenResponse)
async def refresh_session(
    request: RefreshRequest, response: Response, use_case: FromDishka[RefreshSessionUseCase]
) -> AccessTokenResponse:
    response.headers["Cache-Control"] = "no-store"
    result = await use_case.execute(request.refresh_token)
    return AccessTokenResponse(**result.model_dump())


@router.get("/auth/sessions", response_model=list[SessionResponse])
async def get_sessions(
    actor: CurrentActor, use_case: FromDishka[GetSessionsUseCase]
) -> list[SessionResponse]:
    sessions = await use_case.execute(actor)
    return [
        SessionResponse(
            id=session.id,
            expires_at=session.expires_at,
            last_used_at=session.last_used_at,
            created_at=session.created_at,
            is_current=session.id == actor.session_id,
        )
        for session in sessions
    ]


@router.post("/auth/email-verification/resend", status_code=status.HTTP_202_ACCEPTED)
async def resend_verification(
    actor: CurrentActor, use_case: FromDishka[ResendVerificationUseCase]
) -> dict[str, str]:
    await use_case.execute(actor)
    return {"message": "Verification email requested."}


@router.post("/auth/email-verification/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_email(
    request: EmailTokenRequest, use_case: FromDishka[ConfirmEmailUseCase]
) -> Response:
    await use_case.execute(request.token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/auth/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
async def request_password_reset(
    request: PasswordResetRequest, use_case: FromDishka[RequestPasswordResetUseCase]
) -> dict[str, str]:
    await use_case.execute(str(request.email))
    return {"message": "If an active account exists, a password reset email will be sent."}


@router.post("/auth/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def complete_password_reset(
    request: CompletePasswordResetRequest, use_case: FromDishka[CompletePasswordResetUseCase]
) -> Response:
    await use_case.execute(ResetPassword(**request.model_dump()))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
