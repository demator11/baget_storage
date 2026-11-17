from datetime import date, timedelta

from baget_storage.domain.entities.office import NotificationCategory, NotificationResource
from baget_storage.domain.entities.reading_period import ReadingPeriodPolicy
from baget_storage.domain.ports.meter import MeterClock
from baget_storage.domain.ports.reminders import ReminderUnitOfWork
from baget_storage.domain.use_cases.notifications import notify_managers, notify_user
from baget_storage.schemas.office import CreateNotification
from baget_storage.schemas.reminders import ReminderRunResult


class GenerateRemindersUseCase:
    def __init__(
        self, uow: ReminderUnitOfWork, clock: MeterClock, policy: ReadingPeriodPolicy
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._policy = policy

    async def execute(self) -> ReminderRunResult:
        today = self._clock.today()
        result = ReminderRunResult()
        result.invoices_checked = await self._invoices(today)
        result.leases_checked = await self._leases(today)
        current = today.replace(day=1)
        previous = (current - timedelta(days=1)).replace(day=1)
        for period in (previous, current):
            result.readings_checked += await self._readings(period, today)
        return result

    async def _invoices(self, today: date) -> int:
        after = None
        total = 0
        while True:
            async with self._uow:
                invoices = await self._uow.reminders.invoices_due(today, after)
                if not invoices:
                    return total
                for invoice in invoices:
                    overdue = invoice.due_date < today
                    event = "overdue" if overdue else "due"
                    await notify_user(
                        self._uow.notifications,
                        CreateNotification(
                            user_id=invoice.user_id,
                            title="Просрочена оплата счёта" if overdue else "Скоро срок оплаты",
                            body=(
                                f"По счёту {invoice.number} осталось оплатить "
                                f"{invoice.remaining_amount} RUB. Срок: {invoice.due_date}."
                            ),
                            category=NotificationCategory.PAYMENTS,
                            resource_type=NotificationResource.INVOICE,
                            resource_id=invoice.id,
                            deduplication_key=f"invoice-{event}:{invoice.id}:{invoice.due_date}",
                        ),
                    )
                await self._uow.commit()
                total += len(invoices)
                after = invoices[-1].id

    async def _leases(self, today: date) -> int:
        after = None
        total = 0
        while True:
            async with self._uow:
                leases = await self._uow.reminders.leases_ending(today, after)
                if not leases:
                    return total
                for lease in leases:
                    event = f"lease-ending:{lease.id}:{lease.end_date}"
                    title = "Заканчивается срок договора"
                    body = f"Договор {lease.number} действует до {lease.end_date}."
                    await notify_user(
                        self._uow.notifications,
                        CreateNotification(
                            user_id=lease.user_id,
                            title=title,
                            body=body,
                            category=NotificationCategory.LEASES,
                            resource_type=NotificationResource.LEASE,
                            resource_id=lease.id,
                            deduplication_key=f"{event}:{lease.user_id}",
                        ),
                    )
                    await notify_managers(
                        self._uow.notifications,
                        title=title,
                        body=body,
                        category=NotificationCategory.LEASES,
                        resource_type=NotificationResource.LEASE,
                        resource_id=lease.id,
                        event_key=event,
                    )
                await self._uow.commit()
                total += len(leases)
                after = leases[-1].id

    async def _readings(self, period: date, today: date) -> int:
        window = self._policy.window(period, today)
        if not window.is_open:
            return 0
        after = None
        total = 0
        while True:
            async with self._uow:
                meters = await self._uow.reminders.missing_readings(period, after)
                if not meters:
                    return total
                for meter in meters:
                    events = [("opening", "Открыт приём показаний")]
                    if today >= window.closes_on - timedelta(days=2):
                        events.append(("deadline", "Приближается срок сдачи показаний"))
                    for event, title in events:
                        await notify_user(
                            self._uow.notifications,
                            CreateNotification(
                                user_id=meter.user_id,
                                title=title,
                                body=(
                                    f"Передайте показания счётчика {meter.serial_number} "
                                    f"за {period:%m.%Y} до {window.closes_on}."
                                ),
                                category=NotificationCategory.METERS,
                                resource_type=NotificationResource.METER,
                                resource_id=meter.meter_id,
                                deduplication_key=(
                                    f"reading-{event}:{period}:{meter.meter_id}:{meter.user_id}"
                                ),
                            ),
                        )
                await self._uow.commit()
                total += len(meters)
                after = (meters[-1].meter_id, meters[-1].user_id)
