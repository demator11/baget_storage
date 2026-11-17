from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from baget_storage.domain.entities.user import Role


class UserDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str
    role: Role
    company_name: str | None
    phone: str | None
    tax_id: str | None = None
    notify_payments: bool = True
    notify_meters: bool = True
    notify_documents: bool = True
    notify_tickets: bool = True
    notify_leases: bool = True
    email_verified_at: datetime | None = None
    accessible_buildings: list[str] = Field(default_factory=list)
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserCredentialsDTO(UserDTO):
    password_hash: str
    token_version: int


class CreateUser(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128, repr=False)
    full_name: str = Field(min_length=1, max_length=200)
    company_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    tax_id: str | None = Field(default=None, pattern=r"^(?:[0-9]{10}|[0-9]{12})$")

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.casefold()


class StoreUser(BaseModel):
    email: EmailStr
    password_hash: str = Field(repr=False)
    full_name: str
    role: Role
    company_name: str | None = None
    phone: str | None = None
    tax_id: str | None = None
    notify_payments: bool = True
    notify_meters: bool = True
    notify_documents: bool = True
    notify_tickets: bool = True
    notify_leases: bool = True


class UpdateProfile(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    company_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    tax_id: str | None = Field(default=None, pattern=r"^(?:[0-9]{10}|[0-9]{12})$")
    notify_payments: bool | None = None
    notify_meters: bool | None = None
    notify_documents: bool | None = None
    notify_tickets: bool | None = None
    notify_leases: bool | None = None


class Login(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128, repr=False)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.casefold()


class ChangePassword(BaseModel):
    current_password: str = Field(min_length=1, max_length=128, repr=False)
    new_password: str = Field(min_length=12, max_length=128, repr=False)


class TokenClaims(BaseModel):
    user_id: UUID
    token_version: int = Field(ge=0, strict=True)
    session_id: UUID | None = None


class AccessToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserPage(BaseModel):
    items: list[UserDTO]
    total: int
    limit: int
    offset: int


class SessionTokens(AccessToken):
    refresh_token: str = Field(repr=False)
    refresh_expires_in: int
    session_id: UUID
    user: UserDTO


class SessionDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    token_version: int
    expires_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime
    created_at: datetime


class StoreSession(BaseModel):
    user_id: UUID
    token_version: int
    expires_at: datetime


class RefreshTokenDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID
    token_hash: str
    expires_at: datetime
    consumed_at: datetime | None


class EmailActionDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    purpose: str
    token_hash: str
    expires_at: datetime
    consumed_at: datetime | None


class ResetPassword(BaseModel):
    token: str = Field(min_length=32, max_length=256, repr=False)
    new_password: str = Field(min_length=12, max_length=128, repr=False)
