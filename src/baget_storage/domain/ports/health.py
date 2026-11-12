from typing import Protocol


class HealthProbe(Protocol):
    async def ready(self) -> bool: ...
