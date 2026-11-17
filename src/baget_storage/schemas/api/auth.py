from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from baget_storage.domain.entities.user import Role


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=128, repr=False)


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=12, max_length=128, repr=False)
    full_name: str = Field(min_length=1, max_length=200)
    company_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    tax_id: str | None = Field(default=None, pattern=r"^(?:[0-9]{10}|[0-9]{12})$")


class UpdateProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    company_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    tax_id: str | None = Field(default=None, pattern=r"^(?:[0-9]{10}|[0-9]{12})$")
    notify_payments: bool | None = None
    notify_meters: bool | None = None
    notify_documents: bool | None = None
    notify_tickets: bool | None = None
    notify_leases: bool | None = None

    @model_validator(mode="after")
    def validate_full_name(self) -> "UpdateProfileRequest":
        required = {
            "full_name",
            "notify_payments",
            "notify_meters",
            "notify_documents",
            "notify_tickets",
            "notify_leases",
        }
        if any(getattr(self, field) is None for field in required & self.model_fields_set):
            raise ValueError("Required profile fields cannot be null")
        return self


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=128, repr=False)
    new_password: str = Field(min_length=12, max_length=128, repr=False)


class UserResponse(BaseModel):
    id: UUID
    email: EmailStr
    full_name: str
    role: Role
    company_name: str | None
    phone: str | None
    tax_id: str | None
    notify_payments: bool
    notify_meters: bool
    notify_documents: bool
    notify_tickets: bool
    notify_leases: bool
    email_verified_at: datetime | None
    accessible_buildings: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"]
    expires_in: int
    refresh_token: str
    refresh_expires_in: int
    session_id: UUID
    user: UserResponse


class UserPageResponse(BaseModel):
    items: list[UserResponse]
    total: int
    limit: int
    offset: int


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=32, max_length=256, repr=False)


class EmailTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=32, max_length=256, repr=False)


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr


class CompletePasswordResetRequest(EmailTokenRequest):
    new_password: str = Field(min_length=12, max_length=128, repr=False)


class SessionResponse(BaseModel):
    id: UUID
    expires_at: datetime
    last_used_at: datetime
    created_at: datetime
    is_current: bool
