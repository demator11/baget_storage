from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from baget_storage.core.exceptions import AccessDenied, BusinessRuleViolation


class Role(StrEnum):
    MANAGER = "manager"
    TENANT = "tenant"


@dataclass(frozen=True, slots=True)
class Actor:
    id: UUID
    role: Role

    def require_manager(self) -> None:
        if self.role != Role.MANAGER:
            raise AccessDenied()


def validate_password(password: str) -> None:
    if not 12 <= len(password) <= 128:
        raise BusinessRuleViolation("Password must contain between 12 and 128 characters.")


def validate_profile(full_name: str | None) -> None:
    if full_name is None or not full_name.strip() or len(full_name) > 200:
        raise BusinessRuleViolation("Full name must contain between 1 and 200 characters.")
