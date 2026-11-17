from datetime import timedelta

from baget_storage.domain.entities.email import EmailDeliveryError, EmailRetryPolicy
from baget_storage.domain.ports.email import EmailDeliveryStore, EmailSender, WorkerClock
from baget_storage.schemas.email import EmailDeliveryResult


class DeliverEmailsUseCase:
    def __init__(
        self,
        store: EmailDeliveryStore,
        sender: EmailSender,
        clock: WorkerClock,
        policy: EmailRetryPolicy,
    ) -> None:
        self._store = store
        self._sender = sender
        self._clock = clock
        self._policy = policy

    async def execute(self, limit: int = 50) -> EmailDeliveryResult:
        if not 1 <= limit <= 100:
            raise ValueError("Email batch limit must be between 1 and 100")
        result = EmailDeliveryResult()
        for _ in range(limit):
            jobs = await self._store.claim(
                self._clock.now(), 1, self._policy.claim_lease_seconds, self._policy.max_attempts
            )
            if not jobs:
                break
            job = jobs[0]
            result.claimed += 1
            try:
                await self._sender.send(job)
            except Exception as exc:
                permanent = job.attempts >= self._policy.max_attempts
                code = "delivery_error"
                if isinstance(exc, EmailDeliveryError):
                    permanent = permanent or exc.permanent
                    code = exc.code
                await self._store.mark_failed(
                    job.id,
                    job.claim_id,
                    code,
                    self._clock.now() + timedelta(seconds=self._policy.delay(job.attempts)),
                    permanent,
                )
                if permanent:
                    result.failed += 1
                else:
                    result.retrying += 1
            else:
                await self._store.mark_sent(job.id, job.claim_id, self._clock.now())
                result.sent += 1
        return result
