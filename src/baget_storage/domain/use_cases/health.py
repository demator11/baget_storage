from baget_storage.domain.ports.health import HealthProbe


class CheckHealthUseCase:
    def __init__(self, probe: HealthProbe) -> None:
        self._probe = probe

    async def execute(self) -> bool:
        return await self._probe.ready()
