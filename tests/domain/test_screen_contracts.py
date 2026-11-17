from datetime import UTC, date, datetime
from decimal import Decimal
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from baget_storage.core.exceptions import BusinessRuleViolation, EntityNotFound
from baget_storage.domain.entities.rental import LeaseStatus
from baget_storage.domain.entities.user import Actor, Role
from baget_storage.domain.use_cases.office import GetTicketStatsUseCase, GetTicketUseCase
from baget_storage.domain.use_cases.rental import GetExpiringLeasesUseCase, GetLeaseUseCase
from baget_storage.schemas.office import (
    InvoiceFilter,
    PaymentFilter,
    TicketDTO,
    TicketFilter,
    TicketStatsDTO,
    TicketStatsFilter,
)
from baget_storage.schemas.references import RoomRef, TenantRef
from baget_storage.schemas.rental import ExpiringLeaseFilter, LeaseDTO, RentalPage


class FakeLeases:
    def __init__(self, rows: list[LeaseDTO]) -> None:
        self.rows = rows

    async def get_by_id(self, entity_id: UUID) -> LeaseDTO | None:
        return next((row for row in self.rows if row.id == entity_id), None)

    async def expiring(
        self, filters: ExpiringLeaseFilter, tenant_id: UUID | None
    ) -> RentalPage[LeaseDTO]:
        rows = [row for row in self.rows if tenant_id is None or row.tenant_id == tenant_id]
        return RentalPage(
            items=rows[filters.offset : filters.offset + filters.limit],
            total=len(rows),
            limit=filters.limit,
            offset=filters.offset,
        )


class FakeTickets:
    def __init__(self, rows: list[TicketDTO]) -> None:
        self.rows = rows

    async def get_by_id(self, entity_id: UUID) -> TicketDTO | None:
        return next((row for row in self.rows if row.id == entity_id), None)

    async def stats(self, tenant_id: UUID | None, period: date | None = None) -> TicketStatsDTO:
        rows = [
            row
            for row in self.rows
            if (tenant_id is None or row.created_by == tenant_id)
            and (period is None or row.created_at.date().replace(day=1) == period)
        ]
        return TicketStatsDTO(
            total=len(rows),
            new=sum(row.status == "new" for row in rows),
            in_progress=sum(row.status == "in_progress" for row in rows),
            closed=sum(row.status == "closed" for row in rows),
            period=period,
        )


class FakeReadUow:
    def __init__(self, leases: list[LeaseDTO], tickets: list[TicketDTO]) -> None:
        self.leases = FakeLeases(leases)
        self.tickets = FakeTickets(tickets)
        self.active = False

    async def __aenter__(self) -> Self:
        self.active = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.active = False


def lease_for(tenant_id: UUID) -> LeaseDTO:
    now = datetime.now(UTC)
    room = RoomRef(id=uuid4(), name="A-101", building="A", floor=1, area_m2=Decimal("100"))
    return LeaseDTO(
        id=uuid4(),
        number="L-1",
        room_id=room.id,
        tenant_id=tenant_id,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 11, 30),
        monthly_rent=Decimal("15000"),
        status=LeaseStatus.ACTIVE,
        created_at=now,
        updated_at=now,
        room=room,
        tenant=TenantRef(
            id=tenant_id,
            full_name="Tenant",
            role=Role.TENANT,
            company_name="Company",
            email="tenant@example.com",
            phone=None,
        ),
    )


def ticket_for(tenant_id: UUID, status: str = "new") -> TicketDTO:
    now = datetime.now(UTC)
    return TicketDTO(
        id=uuid4(),
        room_id=uuid4(),
        created_by=tenant_id,
        title="Gate repair",
        description="Gate does not close",
        status=status,
        assignee=None,
        created_at=now,
        updated_at=now,
        public_number=1,
        number="REQ-000001",
    )


async def test_lease_detail_returns_screen_references_only_to_owner_or_manager() -> None:
    tenant = Actor(uuid4(), Role.TENANT)
    lease = lease_for(tenant.id)
    uow = FakeReadUow([lease], [])
    result = await GetLeaseUseCase(uow).execute(tenant, lease.id)
    assert result.room is not None and result.room.name == "A-101"
    assert result.tenant is not None and result.tenant.company_name == "Company"
    assert await GetLeaseUseCase(uow).execute(Actor(uuid4(), Role.MANAGER), lease.id) == result
    with pytest.raises(EntityNotFound):
        await GetLeaseUseCase(uow).execute(Actor(uuid4(), Role.TENANT), lease.id)
    assert not uow.active


async def test_expiring_lease_page_cannot_include_other_tenants() -> None:
    tenant = Actor(uuid4(), Role.TENANT)
    own, other = lease_for(tenant.id), lease_for(uuid4())
    uow = FakeReadUow([own, other], [])
    result = await GetExpiringLeasesUseCase(uow).execute(tenant, ExpiringLeaseFilter())
    assert result.total == 1 and result.items == [own]
    manager_result = await GetExpiringLeasesUseCase(uow).execute(
        Actor(uuid4(), Role.MANAGER), ExpiringLeaseFilter(limit=1)
    )
    assert manager_result.total == 2 and len(manager_result.items) == 1


async def test_ticket_detail_and_card_counts_are_tenant_scoped() -> None:
    tenant = Actor(uuid4(), Role.TENANT)
    own = ticket_for(tenant.id)
    foreign = ticket_for(uuid4(), "closed")
    uow = FakeReadUow([], [own, foreign])
    result = await GetTicketUseCase(uow).execute(tenant, own.id)
    assert result.number == "REQ-000001"
    with pytest.raises(EntityNotFound):
        await GetTicketUseCase(uow).execute(tenant, foreign.id)
    assert await GetTicketStatsUseCase(uow).execute(tenant) == TicketStatsDTO(
        total=1, new=1, in_progress=0, closed=0
    )
    assert await GetTicketStatsUseCase(uow).execute(Actor(uuid4(), Role.MANAGER)) == TicketStatsDTO(
        total=2, new=1, in_progress=0, closed=1
    )


async def test_ticket_cards_echo_selected_period_and_keep_all_time_default() -> None:
    tenant = Actor(uuid4(), Role.TENANT)
    november = ticket_for(tenant.id).model_copy(
        update={"created_at": datetime(2026, 11, 1, tzinfo=UTC)}
    )
    october = ticket_for(tenant.id, "closed").model_copy(
        update={"created_at": datetime(2026, 10, 31, tzinfo=UTC)}
    )
    uow = FakeReadUow([], [november, october])
    result = await GetTicketStatsUseCase(uow).execute(tenant, date(2026, 11, 1))
    assert result == TicketStatsDTO(
        total=1, new=1, in_progress=0, closed=0, period=date(2026, 11, 1)
    )
    all_time = await GetTicketStatsUseCase(uow).execute(tenant)
    assert all_time.total == 2 and all_time.period is None
    with pytest.raises(BusinessRuleViolation):
        await GetTicketStatsUseCase(uow).execute(tenant, date(2026, 11, 2))


@pytest.mark.parametrize("model", [TicketFilter, TicketStatsFilter])
def test_ticket_month_filters_require_month_start(model) -> None:
    with pytest.raises(ValidationError):
        model(period="2026-11-02")


@pytest.mark.parametrize(
    "model,values",
    [
        (PaymentFilter, {"paid_from": "2026-11-20", "paid_to": "2026-11-01"}),
        (InvoiceFilter, {"issued_from": "2026-11-20", "issued_to": "2026-11-01"}),
        (InvoiceFilter, {"due_from": "2026-11-20", "due_to": "2026-11-01"}),
        (InvoiceFilter, {"period": "2026-11-02"}),
        (PaymentFilter, {"period": "2026-11-02"}),
    ],
)
def test_date_filters_reject_reversed_ranges_and_partial_months(model, values) -> None:
    with pytest.raises(ValidationError):
        model(**values)


def test_all_periods_and_exact_date_bounds_remain_valid() -> None:
    assert PaymentFilter().period is None
    assert InvoiceFilter().period is None
    assert PaymentFilter(paid_from="2026-11-01", paid_to="2026-11-01").paid_to == date(2026, 11, 1)
    assert InvoiceFilter(period="2026-11-01").period == date(2026, 11, 1)
