from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from baget_storage.core.exceptions import AccessDenied, EntityNotFound
from baget_storage.domain.entities.office import (
    NotificationCategory,
    NotificationPreferences,
    NotificationResource,
)
from baget_storage.domain.entities.user import Actor, Role
from baget_storage.domain.use_cases.notification_queries import GetNotificationUseCase
from baget_storage.domain.use_cases.notifications import notify_managers, notify_user
from baget_storage.domain.use_cases.office import CreateNotificationUseCase
from baget_storage.schemas.office import CreateNotification, NotificationDTO


class FakeNotificationRepository:
    def __init__(self):
        self.items = {}
        self.keys = {}
        self.email = None
        self.emails = {}
        self.settings = NotificationPreferences()
        self.managers = []
        self.fail_enqueue = False
        self.allowed_resources = set()

    async def create(self, data):
        if data.deduplication_key in self.keys:
            return self.items[self.keys[data.deduplication_key]]
        notification = NotificationDTO(
            **data.model_dump(), id=uuid4(), read_at=None, created_at=datetime.now(UTC)
        )
        self.items[notification.id] = notification
        if data.deduplication_key is not None:
            self.keys[data.deduplication_key] = notification.id
        return notification

    async def preferences(self, user_id):
        return self.settings

    async def verified_email(self, user_id):
        return self.email

    async def enqueue_email(self, notification, recipient):
        if self.fail_enqueue:
            raise RuntimeError("Outbox unavailable")
        self.emails[notification.id] = recipient

    async def manager_ids(self, after=None, limit=100):
        return [manager for manager in sorted(self.managers) if after is None or manager > after][
            :limit
        ]

    async def get_by_id(self, user_id, notification_id):
        item = self.items.get(notification_id)
        return item if item is not None and item.user_id == user_id else None

    async def can_access_resource(self, user_id, resource_type, resource_id):
        return (user_id, resource_type, resource_id) in self.allowed_resources


class FakeNotificationUow:
    def __init__(self):
        self.notifications = FakeNotificationRepository()
        self.committed = False
        self.access = SimpleNamespace(user_exists=self.user_exists)

    async def user_exists(self, user_id):
        return True

    async def __aenter__(self):
        self.snapshot = deepcopy(self.notifications)
        self.committed = False
        return self

    async def __aexit__(self, *args):
        if not self.committed:
            self.notifications = self.snapshot

    async def commit(self):
        self.committed = True


@pytest.mark.parametrize("verified", [False, True])
async def test_disabled_email_preferences_do_not_suppress_in_app_notifications(verified):
    repository = FakeNotificationRepository()
    repository.settings = NotificationPreferences(notify_payments=False)
    repository.email = "tenant@example.com" if verified else None
    result = await notify_user(
        repository,
        CreateNotification(
            user_id=uuid4(),
            title="Invoice",
            body="Payment due",
            category=NotificationCategory.PAYMENTS,
        ),
    )
    assert repository.items == {result.id: result}
    assert repository.emails == {}


async def test_verified_enabled_email_is_enqueued_once_for_replayed_event():
    repository = FakeNotificationRepository()
    repository.email = "tenant@example.com"
    data = CreateNotification(
        user_id=uuid4(), title="Invoice", body="Payment due", deduplication_key="event-1"
    )
    first = await notify_user(repository, data)
    second = await notify_user(repository, data)
    assert first.id == second.id
    assert len(repository.items) == len(repository.emails) == 1


async def test_unverified_address_never_receives_ordinary_email():
    repository = FakeNotificationRepository()
    await notify_user(repository, CreateNotification(user_id=uuid4(), title="Title", body="Body"))
    assert len(repository.items) == 1
    assert not repository.emails


async def test_manager_audience_is_paginated_and_deduplicated():
    repository = FakeNotificationRepository()
    repository.managers = [UUID(int=index) for index in range(1, 206)]
    kwargs = {
        "title": "New ticket",
        "body": "A tenant needs assistance",
        "category": NotificationCategory.TICKETS,
        "resource_type": NotificationResource.TICKET,
        "resource_id": uuid4(),
        "event_key": "ticket-created:1",
    }
    count = await notify_managers(repository, **kwargs)
    await notify_managers(repository, **kwargs)
    assert count == 205
    assert {item.user_id for item in repository.items.values()} == set(repository.managers)
    assert len(repository.items) == 205


async def test_manual_notification_checks_recipient_resource_access():
    uow = FakeNotificationUow()
    manager = Actor(id=uuid4(), role=Role.MANAGER)
    target = uuid4()
    resource_id = uuid4()
    data = CreateNotification(
        user_id=target,
        title="Invoice",
        body="Due",
        resource_type=NotificationResource.INVOICE,
        resource_id=resource_id,
    )
    with pytest.raises(AccessDenied):
        await CreateNotificationUseCase(uow).execute(manager, data)
    assert not uow.notifications.items
    uow.notifications.allowed_resources.add((target, NotificationResource.INVOICE, resource_id))
    result = await CreateNotificationUseCase(uow).execute(manager, data)
    assert result.resource_id == resource_id


async def test_notification_detail_is_owned_by_recipient_even_for_manager():
    uow = FakeNotificationUow()
    owner = Actor(id=uuid4(), role=Role.TENANT)
    notification = await uow.notifications.create(
        CreateNotification(user_id=owner.id, title="Title", body="Body")
    )
    result = await GetNotificationUseCase(uow).execute(owner, notification.id)
    assert result.id == notification.id
    with pytest.raises(EntityNotFound):
        await GetNotificationUseCase(uow).execute(
            Actor(id=uuid4(), role=Role.MANAGER), notification.id
        )


async def test_in_app_and_email_outbox_roll_back_together():
    uow = FakeNotificationUow()
    uow.notifications.email = "tenant@example.com"
    uow.notifications.fail_enqueue = True
    with pytest.raises(RuntimeError, match="Outbox unavailable"):
        await CreateNotificationUseCase(uow).execute(
            Actor(id=uuid4(), role=Role.MANAGER),
            CreateNotification(user_id=uuid4(), title="Title", body="Body"),
        )
    assert not uow.notifications.items
    assert not uow.notifications.emails
