from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from tests.domain.test_office import FakeNotifications

from baget_storage.core.exceptions import AccessDenied, Conflict, EntityNotFound
from baget_storage.domain.entities.office import NotificationResource, TicketStatus
from baget_storage.domain.entities.user import Actor, Role
from baget_storage.domain.use_cases.office import TakeTicketUseCase
from baget_storage.schemas.office import TicketDTO


class FakeAccess:
    def __init__(self, manager_id):
        self.manager_id = manager_id

    async def user_name(self, user_id):
        return "Current Manager" if user_id == self.manager_id else None


class FakeTickets:
    def __init__(self, ticket):
        self.ticket = ticket
        self.events = []
        self.locked = False

    async def lock(self, ticket_id):
        self.locked = True
        return self.ticket if ticket_id == self.ticket.id else None

    async def update(self, ticket_id, data):
        assert self.locked
        self.ticket = self.ticket.model_copy(update=data.model_dump(exclude_unset=True))
        return self.ticket

    async def add_event(self, ticket_id, actor_id, description):
        self.events.append((ticket_id, actor_id, description))


class FakeUow:
    def __init__(self, manager_id, ticket):
        self.access = FakeAccess(manager_id)
        self.tickets = FakeTickets(ticket)
        self.notifications = FakeNotifications()
        self.committed = False

    async def __aenter__(self):
        self.snapshot = deepcopy((self.tickets, self.notifications))
        self.committed = False
        return self

    async def __aexit__(self, *args):
        if not self.committed:
            self.tickets, self.notifications = self.snapshot

    async def commit(self):
        self.committed = True


@pytest.fixture
def context():
    manager = Actor(uuid4(), Role.MANAGER)
    tenant = Actor(uuid4(), Role.TENANT)
    ticket = TicketDTO(
        id=uuid4(),
        room_id=uuid4(),
        created_by=tenant.id,
        title="Gate repair",
        description="Gate is stuck",
        status=TicketStatus.NEW,
        assignee=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    return SimpleNamespace(
        manager=manager, tenant=tenant, ticket=ticket, uow=FakeUow(manager.id, ticket)
    )


async def test_take_ticket_assigns_current_manager_and_records_event(context):
    result = await TakeTicketUseCase(context.uow).execute(context.manager, context.ticket.id)
    assert result.status == TicketStatus.IN_PROGRESS
    assert result.assignee == "Current Manager"
    assert context.uow.tickets.events == [
        (
            context.ticket.id,
            context.manager.id,
            "Status: in_progress; assignee: Current Manager",
        )
    ]
    notice = context.uow.notifications.items[0]
    assert notice.user_id == context.tenant.id
    assert notice.resource_type == NotificationResource.TICKET
    assert notice.resource_id == context.ticket.id
    assert context.uow.committed


async def test_tenant_cannot_take_ticket(context):
    with pytest.raises(AccessDenied):
        await TakeTicketUseCase(context.uow).execute(context.tenant, context.ticket.id)
    assert not context.uow.tickets.locked


@pytest.mark.parametrize("status", [TicketStatus.IN_PROGRESS, TicketStatus.CLOSED])
async def test_only_new_tickets_can_be_taken(context, status):
    context.uow.tickets.ticket = context.ticket.model_copy(update={"status": status})
    with pytest.raises(Conflict):
        await TakeTicketUseCase(context.uow).execute(context.manager, context.ticket.id)
    assert context.uow.tickets.events == []
    assert context.uow.notifications.items == []


async def test_unknown_ticket_is_not_found(context):
    with pytest.raises(EntityNotFound):
        await TakeTicketUseCase(context.uow).execute(context.manager, uuid4())


async def test_notification_failure_rolls_back_assignment_and_history(context):
    context.uow.notifications.fail = True
    with pytest.raises(RuntimeError):
        await TakeTicketUseCase(context.uow).execute(context.manager, context.ticket.id)
    assert context.uow.tickets.ticket.status == TicketStatus.NEW
    assert context.uow.tickets.ticket.assignee is None
    assert context.uow.tickets.events == []
    assert not context.uow.committed
