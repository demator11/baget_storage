from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from baget_storage.core.exceptions import EntityNotFound
from baget_storage.domain.entities.office import (
    NotificationCategory,
    NotificationPreferences,
    NotificationResource,
)
from baget_storage.infrastructure.postgres.models.meter import MeterOrm, MeterReadingOrm
from baget_storage.infrastructure.postgres.models.office import (
    DocumentOrm,
    InvoiceOrm,
    NotificationOrm,
    TicketOrm,
)
from baget_storage.infrastructure.postgres.models.rental import LeaseOrm
from baget_storage.infrastructure.postgres.models.user import UserOrm
from baget_storage.infrastructure.postgres.repository.email import SqlAlchemyEmailOutbox
from baget_storage.schemas.office import (
    CreateNotification,
    NotificationCountsDTO,
    NotificationDTO,
    NotificationFilter,
    Page,
)


class SqlAlchemyNotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._outbox = SqlAlchemyEmailOutbox(session)

    async def preferences(self, user_id: UUID) -> NotificationPreferences:
        row = await self._session.get(UserOrm, user_id)
        if row is None:
            raise EntityNotFound("User not found")
        return NotificationPreferences(
            notify_payments=row.notify_payments,
            notify_meters=row.notify_meters,
            notify_documents=row.notify_documents,
            notify_tickets=row.notify_tickets,
            notify_leases=row.notify_leases,
        )

    async def verified_email(self, user_id: UUID) -> str | None:
        return await self._session.scalar(
            select(UserOrm.email).where(
                UserOrm.id == user_id,
                UserOrm.is_active.is_(True),
                UserOrm.email_verified_at.is_not(None),
            )
        )

    async def enqueue_email(self, notification: NotificationDTO, recipient: str) -> None:
        payload = {"title": notification.title, "body": notification.body}
        if notification.resource_type is not None and notification.resource_id is not None:
            payload.update(
                resource_type=notification.resource_type.value,
                resource_id=str(notification.resource_id),
            )
        await self._outbox.enqueue(
            recipient, "notification", payload, f"notification:{notification.id}"
        )

    async def create(self, data: CreateNotification) -> NotificationDTO:
        statement = insert(NotificationOrm).values(
            **data.model_dump(), deduplication_key=data.deduplication_key
        )
        if data.deduplication_key is not None:
            statement = statement.on_conflict_do_nothing(
                index_elements=[NotificationOrm.deduplication_key]
            )
        row = await self._session.scalar(statement.returning(NotificationOrm))
        if row is None:
            row = await self._session.scalar(
                select(NotificationOrm).where(
                    NotificationOrm.deduplication_key == data.deduplication_key
                )
            )
        if row is None:
            raise RuntimeError("Notification could not be stored")
        return NotificationDTO.model_validate(row)

    async def get_by_id(self, user_id: UUID, notification_id: UUID) -> NotificationDTO | None:
        row = await self._session.scalar(
            select(NotificationOrm).where(
                NotificationOrm.id == notification_id, NotificationOrm.user_id == user_id
            )
        )
        return NotificationDTO.model_validate(row) if row is not None else None

    async def counts(self, user_id: UUID) -> NotificationCountsDTO:
        rows = await self._session.execute(
            select(
                NotificationOrm.category,
                func.count().label("total"),
                func.count().filter(NotificationOrm.read_at.is_(None)).label("unread"),
            )
            .where(NotificationOrm.user_id == user_id)
            .group_by(NotificationOrm.category)
        )
        categories = dict.fromkeys(NotificationCategory, 0)
        unread = dict.fromkeys(NotificationCategory, 0)
        for row in rows:
            categories[NotificationCategory(row.category)] = row.total
            unread[NotificationCategory(row.category)] = row.unread
        return NotificationCountsDTO(
            total=sum(categories.values()),
            unread=sum(unread.values()),
            categories=categories,
            unread_by_category=unread,
        )

    async def manager_ids(self, after: UUID | None = None, limit: int = 100) -> list[UUID]:
        if not 1 <= limit <= 100:
            raise ValueError("Invalid manager page limit")
        query = select(UserOrm.id).where(UserOrm.role == "manager", UserOrm.is_active.is_(True))
        if after is not None:
            query = query.where(UserOrm.id > after)
        return list(await self._session.scalars(query.order_by(UserOrm.id).limit(limit)))

    async def can_access_resource(
        self, user_id: UUID, resource_type: NotificationResource, resource_id: UUID
    ) -> bool:
        user = await self._session.get(UserOrm, user_id)
        if user is None or not user.is_active:
            return False
        model = {
            NotificationResource.INVOICE: InvoiceOrm,
            NotificationResource.DOCUMENT: DocumentOrm,
            NotificationResource.TICKET: TicketOrm,
            NotificationResource.LEASE: LeaseOrm,
            NotificationResource.METER: MeterOrm,
            NotificationResource.READING: MeterReadingOrm,
        }[resource_type]
        query = select(model.id).where(model.id == resource_id)
        if user.role != "manager":
            if resource_type in {NotificationResource.INVOICE, NotificationResource.DOCUMENT}:
                query = query.join(LeaseOrm).where(LeaseOrm.tenant_id == user_id)
            elif resource_type is NotificationResource.LEASE:
                query = query.where(LeaseOrm.tenant_id == user_id)
            elif resource_type is NotificationResource.TICKET:
                query = query.where(TicketOrm.created_by == user_id)
            elif resource_type is NotificationResource.READING:
                query = query.where(MeterReadingOrm.submitted_by == user_id)
            else:
                today = datetime.now(UTC).date()
                query = query.join(LeaseOrm, LeaseOrm.room_id == MeterOrm.room_id).where(
                    LeaseOrm.tenant_id == user_id,
                    LeaseOrm.status == "active",
                    LeaseOrm.start_date <= today,
                    LeaseOrm.end_date >= today,
                )
        return await self._session.scalar(query.limit(1)) is not None

    async def list(self, user_id: UUID, filters: NotificationFilter) -> Page[NotificationDTO]:
        query = select(NotificationOrm).where(NotificationOrm.user_id == user_id)
        if filters.category is not None:
            query = query.where(NotificationOrm.category == filters.category)
        if filters.unread_only:
            query = query.where(NotificationOrm.read_at.is_(None))
        if filters.search:
            query = query.where(
                or_(
                    NotificationOrm.title.icontains(filters.search, autoescape=True),
                    NotificationOrm.body.icontains(filters.search, autoescape=True),
                )
            )
        total = await self._session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await self._session.scalars(
            query.order_by(NotificationOrm.created_at.desc(), NotificationOrm.id)
            .limit(filters.limit)
            .offset(filters.offset)
        )
        return Page(
            items=[NotificationDTO.model_validate(row) for row in rows],
            total=total or 0,
            limit=filters.limit,
            offset=filters.offset,
        )

    async def mark_read(self, user_id: UUID, notification_id: UUID | None) -> int:
        query = update(NotificationOrm).where(NotificationOrm.user_id == user_id)
        if notification_id is not None:
            query = query.where(NotificationOrm.id == notification_id)
        else:
            query = query.where(NotificationOrm.read_at.is_(None))
        ids = await self._session.scalars(
            query.values(read_at=func.coalesce(NotificationOrm.read_at, func.now())).returning(
                NotificationOrm.id
            )
        )
        return len(ids.all())
