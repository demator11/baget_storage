from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AuthPolicy:
    frontend_base_url: str
    refresh_days: int = 30
    verification_hours: int = 24
    password_reset_minutes: int = 30
