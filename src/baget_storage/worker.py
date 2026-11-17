import asyncio
import logging
from datetime import UTC, datetime

from baget_storage.core.config import Settings
from baget_storage.core.providers.setup import create_container
from baget_storage.domain.use_cases.email import DeliverEmailsUseCase
from baget_storage.domain.use_cases.reminders import GenerateRemindersUseCase

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = Settings()
    container = create_container(settings)
    last_reminder_day = None
    try:
        while True:
            try:
                async with container() as scope:
                    today = datetime.now(UTC).date()
                    if last_reminder_day != today:
                        reminders = await scope.get(GenerateRemindersUseCase)
                        await reminders.execute()
                        last_reminder_day = today
                    delivery = await scope.get(DeliverEmailsUseCase)
                    result = await delivery.execute(limit=settings.worker_batch_size)
                    if result.claimed:
                        logger.info(
                            "Email delivery batch: sent=%s retrying=%s failed=%s",
                            result.sent,
                            result.retrying,
                            result.failed,
                        )
            except Exception:
                logger.exception("Background delivery iteration failed")
            await asyncio.sleep(settings.worker_poll_seconds)
    finally:
        await container.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
