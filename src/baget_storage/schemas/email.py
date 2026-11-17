from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class EmailJob(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    recipient: EmailStr
    template: Literal["notification", "verify_email", "password_reset"]
    payload: dict[str, str]
    attempts: int = Field(ge=1)
    claim_id: UUID
    expires_at: datetime | None


class EmailDeliveryResult(BaseModel):
    claimed: int = 0
    sent: int = 0
    retrying: int = 0
    failed: int = 0
