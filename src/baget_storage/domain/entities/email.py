from dataclasses import dataclass


class EmailDeliveryError(Exception):
    def __init__(self, code: str, *, permanent: bool = False) -> None:
        self.code = code
        self.permanent = permanent
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EmailRetryPolicy:
    max_attempts: int = 6
    base_delay_seconds: int = 30
    maximum_delay_seconds: int = 3600
    claim_lease_seconds: int = 300

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.base_delay_seconds < 1:
            raise ValueError("Email retry limits must be positive")
        if self.maximum_delay_seconds < self.base_delay_seconds or self.claim_lease_seconds < 1:
            raise ValueError("Invalid email delivery policy")

    def delay(self, attempts: int) -> int:
        return min(self.maximum_delay_seconds, self.base_delay_seconds * 2 ** min(attempts - 1, 20))
