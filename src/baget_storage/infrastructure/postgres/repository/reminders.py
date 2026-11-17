from calendar import monthrange
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from baget_storage.infrastructure.postgres.models.meter import MeterOrm, MeterReadingOrm
from baget_storage.infrastructure.postgres.models.office import InvoiceOrm, PaymentOrm
from baget_storage.infrastructure.postgres.models.rental import LeaseOrm
from baget_storage.infrastructure.postgres.models.user import UserOrm
from baget_storage.schemas.reminders import InvoiceReminder, LeaseReminder, ReadingReminder


class SqlAlchemyReminderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def invoices_due(
        self, today: date, after: UUID | None = None, limit: int = 100
    ) -> list[InvoiceReminder]:
        paid = (
            select(func.coalesce(func.sum(PaymentOrm.amount), 0))
            .where(PaymentOrm.invoice_id == InvoiceOrm.id)
            .correlate(InvoiceOrm)
            .scalar_subquery()
        )
        query = (
            select(
                InvoiceOrm.id,
                LeaseOrm.tenant_id.label("user_id"),
                InvoiceOrm.number,
                InvoiceOrm.due_date,
                (InvoiceOrm.amount - paid).label("remaining_amount"),
            )
            .join(LeaseOrm)
            .join(UserOrm, UserOrm.id == LeaseOrm.tenant_id)
            .where(
                InvoiceOrm.amount > paid,
                InvoiceOrm.due_date <= today + timedelta(days=3),
                UserOrm.is_active.is_(True),
            )
        )
        if after is not None:
            query = query.where(InvoiceOrm.id > after)
        rows = await self._session.execute(query.order_by(InvoiceOrm.id).limit(limit))
        return [InvoiceReminder.model_validate(row._mapping) for row in rows]

    async def leases_ending(
        self, today: date, after: UUID | None = None, limit: int = 100
    ) -> list[LeaseReminder]:
        query = (
            select(
                LeaseOrm.id,
                LeaseOrm.tenant_id.label("user_id"),
                LeaseOrm.number,
                LeaseOrm.end_date,
            )
            .join(UserOrm, UserOrm.id == LeaseOrm.tenant_id)
            .where(
                LeaseOrm.status == "active",
                LeaseOrm.end_date >= today,
                LeaseOrm.end_date <= today + timedelta(days=30),
                UserOrm.is_active.is_(True),
            )
        )
        if after is not None:
            query = query.where(LeaseOrm.id > after)
        rows = await self._session.execute(query.order_by(LeaseOrm.id).limit(limit))
        return [LeaseReminder.model_validate(row._mapping) for row in rows]

    async def missing_readings(
        self, period: date, after: tuple[UUID, UUID] | None = None, limit: int = 100
    ) -> list[ReadingReminder]:
        last_day = period.replace(day=monthrange(period.year, period.month)[1])
        submitted = (
            select(MeterReadingOrm.id)
            .where(
                MeterReadingOrm.meter_id == MeterOrm.id,
                MeterReadingOrm.period == period,
                MeterReadingOrm.status.in_(["pending", "accepted"]),
            )
            .exists()
        )
        query = (
            select(
                MeterOrm.id.label("meter_id"),
                LeaseOrm.tenant_id.label("user_id"),
                MeterOrm.serial_number,
            )
            .join(LeaseOrm, LeaseOrm.room_id == MeterOrm.room_id)
            .join(UserOrm, UserOrm.id == LeaseOrm.tenant_id)
            .where(
                LeaseOrm.status == "active",
                LeaseOrm.start_date <= last_day,
                LeaseOrm.end_date >= period,
                UserOrm.is_active.is_(True),
                ~submitted,
            )
            .distinct()
        )
        if after is not None:
            query = query.where(tuple_(MeterOrm.id, LeaseOrm.tenant_id) > after)
        rows = await self._session.execute(
            query.order_by(MeterOrm.id, LeaseOrm.tenant_id).limit(limit)
        )
        return [ReadingReminder.model_validate(row._mapping) for row in rows]
