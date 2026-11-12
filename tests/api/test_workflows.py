from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from baget_storage.app import create_app
from baget_storage.core.config import Settings
from baget_storage.domain.entities.user import Role
from baget_storage.infrastructure.postgres.auth_uow import AuthSqlAlchemyUnitOfWork
from baget_storage.infrastructure.security import Argon2PasswordHasher
from baget_storage.schemas.user import StoreUser

pytestmark = pytest.mark.integration
PASSWORD = "test-password-long-enough"


@dataclass
class ApiContext:
    client: httpx.AsyncClient
    manager: dict[str, str]
    tenant: dict[str, str]
    other: dict[str, str]
    tenant_id: str
    other_id: str


@dataclass
class RentalContext:
    api: ApiContext
    room_id: str
    lease_id: str


async def login(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
async def api(
    session_factory: async_sessionmaker[AsyncSession], test_database_url: str
) -> AsyncIterator[ApiContext]:
    passwords = Argon2PasswordHasher()
    async with AuthSqlAlchemyUnitOfWork(session_factory) as uow:
        await uow.users.create(
            StoreUser(
                email="manager@example.com",
                full_name="Test Manager",
                password_hash=await passwords.hash(PASSWORD),
                role=Role.MANAGER,
            )
        )
        await uow.commit()
    app = create_app(
        Settings(
            database_url=test_database_url,
            jwt_secret="api-test-key-" * 4,
            environment="test",
        )
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            manager = await login(client, "manager@example.com")
            users = []
            for email in ("tenant@example.com", "other@example.com"):
                response = await client.post(
                    "/api/v1/users",
                    headers=manager,
                    json={
                        "email": email,
                        "password": PASSWORD,
                        "full_name": email.split("@")[0],
                        "company_name": "Company",
                        "phone": "+79000000000",
                    },
                )
                assert response.status_code == 201, response.text
                assert "password_hash" not in response.json()
                users.append(response.json()["id"])
            yield ApiContext(
                client=client,
                manager=manager,
                tenant=await login(client, "tenant@example.com"),
                other=await login(client, "other@example.com"),
                tenant_id=users[0],
                other_id=users[1],
            )


@pytest.fixture
async def rental(api: ApiContext) -> RentalContext:
    room = await api.client.post(
        "/api/v1/rooms",
        headers=api.manager,
        json={
            "name": "B2",
            "building": "B",
            "floor": 1,
            "area_m2": "350.00",
            "monthly_rent": "70000.00",
        },
    )
    assert room.status_code == 201, room.text
    today = datetime.now(UTC).date()
    lease = await api.client.post(
        "/api/v1/leases",
        headers=api.manager,
        json={
            "number": "LEASE-001",
            "room_id": room.json()["id"],
            "tenant_id": api.tenant_id,
            "start_date": (today - timedelta(days=90)).isoformat(),
            "end_date": (today + timedelta(days=365)).isoformat(),
            "monthly_rent": "65000.00",
        },
    )
    assert lease.status_code == 201, lease.text
    return RentalContext(api=api, room_id=room.json()["id"], lease_id=lease.json()["id"])


async def test_auth_profile_authorization_and_logout(api: ApiContext) -> None:
    assert (await api.client.get("/health/live")).json() == {"status": "ok"}
    assert (await api.client.get("/health/ready")).status_code == 200
    unauthenticated = await api.client.get("/api/v1/users/me")
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["www-authenticate"] == "Bearer"
    assert (await api.client.get("/api/v1/users", headers=api.tenant)).status_code == 403
    users = await api.client.get("/api/v1/users", headers=api.manager)
    assert users.status_code == 200
    assert users.json()["total"] == 2

    profile = await api.client.patch(
        "/api/v1/users/me",
        headers=api.tenant,
        json={"phone": None, "tax_id": "1234567890", "notify_meters": False},
    )
    assert profile.status_code == 200, profile.text
    assert profile.json()["phone"] is None
    assert profile.json()["company_name"] == "Company"
    assert profile.json()["tax_id"] == "1234567890"
    assert not profile.json()["notify_meters"]
    assert profile.json()["notify_documents"]
    for payload in ({"full_name": None}, {"notify_meters": None}, {"role": "manager"}):
        response = await api.client.patch("/api/v1/users/me", headers=api.tenant, json=payload)
        assert response.status_code == 422, response.text

    private_password = "private-password-must-never-appear"
    invalid = await api.client.post(
        "/api/v1/auth/login", json={"email": "not-an-email", "password": private_password}
    )
    assert invalid.status_code == 422
    assert private_password not in invalid.text
    assert "input" not in invalid.text
    forged = await api.client.get(
        "/api/v1/users/me", headers={"Authorization": "Bearer malformed-token"}
    )
    assert forged.status_code == 401

    logout = await api.client.post("/api/v1/auth/logout", headers=api.tenant)
    assert logout.status_code == 204
    assert (await api.client.get("/api/v1/users/me", headers=api.tenant)).status_code == 401
    new_headers = await login(api.client, "tenant@example.com")
    assert (await api.client.get("/api/v1/users/me", headers=new_headers)).status_code == 200


async def test_rental_and_meter_workflow_keeps_tenants_isolated(rental: RentalContext) -> None:
    api = rental.api
    own_rooms = await api.client.get("/api/v1/rooms", headers=api.tenant)
    assert own_rooms.status_code == 200, own_rooms.text
    assert own_rooms.json()["items"][0]["id"] == rental.room_id
    foreign_rooms = await api.client.get("/api/v1/rooms", headers=api.other)
    assert foreign_rooms.json()["total"] == 0
    assert (
        await api.client.get(f"/api/v1/rooms/{rental.room_id}", headers=api.other)
    ).status_code == 404
    assert (await api.client.get("/api/v1/leases", headers=api.other)).json()["total"] == 0
    forbidden = await api.client.patch(
        f"/api/v1/rooms/{rental.room_id}", headers=api.tenant, json={"monthly_rent": "1.00"}
    )
    assert forbidden.status_code == 403
    meter = await api.client.post(
        "/api/v1/meters",
        headers=api.manager,
        json={
            "room_id": rental.room_id,
            "kind": "electricity",
            "serial_number": "EL-001",
            "initial_reading": "100.000",
        },
    )
    assert meter.status_code == 201, meter.text
    meter_id = meter.json()["id"]
    period = datetime.now(UTC).date().replace(day=1).isoformat()
    forbidden_reading = await api.client.post(
        f"/api/v1/meters/{meter_id}/readings",
        headers=api.other,
        json={"period": period, "value": "140.000"},
    )
    assert forbidden_reading.status_code == 403
    reading = await api.client.post(
        f"/api/v1/meters/{meter_id}/readings",
        headers=api.tenant,
        json={"period": period, "value": "140.000"},
    )
    assert reading.status_code == 201, reading.text
    assert Decimal(reading.json()["consumption"]) == Decimal("40")
    review = await api.client.post(
        f"/api/v1/readings/{reading.json()['id']}/review",
        headers=api.manager,
        json={"status": "accepted"},
    )
    assert review.status_code == 200, review.text
    assert review.json()["status"] == "accepted"
    own = await api.client.get("/api/v1/meters", headers=api.tenant)
    assert own.json()["items"][0]["latest_reading_status"] == "accepted"
    assert (await api.client.get("/api/v1/meters", headers=api.other)).json()["total"] == 0
    notifications = await api.client.get(
        "/api/v1/notifications?category=meters", headers=api.tenant
    )
    assert notifications.status_code == 200, notifications.text
    assert notifications.json()["total"] == 1


async def test_billing_documents_tickets_and_dashboard(rental: RentalContext) -> None:
    api = rental.api
    today = datetime.now(UTC).date()
    invoice_body = {
        "lease_id": rental.lease_id,
        "number": "INV-001",
        "description": "Monthly rent",
        "amount": "100.00",
        "issued_on": (today - timedelta(days=10)).isoformat(),
        "due_date": (today - timedelta(days=1)).isoformat(),
    }
    forbidden = await api.client.post("/api/v1/invoices", headers=api.tenant, json=invoice_body)
    assert forbidden.status_code == 403
    invoice = await api.client.post("/api/v1/invoices", headers=api.manager, json=invoice_body)
    assert invoice.status_code == 201, invoice.text
    assert invoice.json()["status"] == "overdue"
    invoice_id = invoice.json()["id"]
    own = await api.client.get("/api/v1/invoices?status=overdue", headers=api.tenant)
    assert own.status_code == 200, own.text
    assert own.json()["total"] == 1
    assert (await api.client.get("/api/v1/invoices", headers=api.other)).json()["total"] == 0

    payment_body = {"amount": "60.00", "reference": "BANK-001", "paid_on": today.isoformat()}
    payment = await api.client.post(
        f"/api/v1/invoices/{invoice_id}/payments", headers=api.manager, json=payment_body
    )
    assert payment.status_code == 201, payment.text
    repeated = await api.client.post(
        f"/api/v1/invoices/{invoice_id}/payments", headers=api.manager, json=payment_body
    )
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["id"] == payment.json()["id"]
    overpayment = await api.client.post(
        f"/api/v1/invoices/{invoice_id}/payments",
        headers=api.manager,
        json={**payment_body, "reference": "BANK-002", "amount": "41.00"},
    )
    assert overpayment.status_code == 422
    payment_notifications = await api.client.get(
        "/api/v1/notifications?category=payments", headers=api.tenant
    )
    assert payment_notifications.json()["total"] == 2
    assert (await api.client.get("/api/v1/payments", headers=api.tenant)).json()["total"] == 1
    assert (await api.client.get("/api/v1/payments", headers=api.other)).json()["total"] == 0

    pdf = b"%PDF-1.7\nTest document\n%%EOF"
    document = await api.client.post(
        "/api/v1/documents",
        headers=api.manager,
        data={
            "lease_id": rental.lease_id,
            "title": "Lease agreement",
            "kind": "contract",
            "status": "signed",
            "issued_on": today.isoformat(),
        },
        files={"file": ("agreement.pdf", pdf, "application/pdf")},
    )
    assert document.status_code == 201, document.text
    assert document.json()["status"] == "signed"
    assert document.json()["size_bytes"] == len(pdf)
    assert "content" not in document.json()
    document_id = document.json()["id"]
    download = await api.client.get(f"/api/v1/documents/{document_id}/download", headers=api.tenant)
    assert download.status_code == 200, download.text
    assert download.content == pdf
    assert download.headers["content-disposition"].startswith("attachment;")
    assert (
        await api.client.get(f"/api/v1/documents/{document_id}/download", headers=api.other)
    ).status_code == 404
    assert (await api.client.get("/api/v1/documents", headers=api.other)).json()["total"] == 0

    ticket = await api.client.post(
        "/api/v1/tickets",
        headers=api.tenant,
        json={"room_id": rental.room_id, "title": "Gate repair", "description": "Gate is stuck"},
    )
    assert ticket.status_code == 201, ticket.text
    ticket_id = ticket.json()["id"]
    updated = await api.client.patch(
        f"/api/v1/tickets/{ticket_id}",
        headers=api.manager,
        json={"status": "in_progress", "assignee": "Technician"},
    )
    assert updated.status_code == 200, updated.text
    history = await api.client.get(f"/api/v1/tickets/{ticket_id}/history", headers=api.tenant)
    assert history.status_code == 200, history.text
    assert history.json()["total"] == 2
    assert (
        await api.client.get(f"/api/v1/tickets/{ticket_id}/history", headers=api.other)
    ).status_code == 404
    assert (await api.client.get("/api/v1/tickets", headers=api.other)).json()["total"] == 0

    dashboard = await api.client.get("/api/v1/dashboard", headers=api.tenant)
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["rooms_count"] == 1
    assert Decimal(dashboard.json()["area_m2"]) == Decimal("350")
    assert Decimal(dashboard.json()["monthly_rent"]) == Decimal("65000")
    assert Decimal(dashboard.json()["unpaid_amount"]) == Decimal("40")
    assert dashboard.json()["open_tickets"] == 1
    assert dashboard.json()["unread_notifications"] == 4
    foreign_dashboard = await api.client.get("/api/v1/dashboard", headers=api.other)
    assert foreign_dashboard.json()["rooms_count"] == 0
    assert Decimal(foreign_dashboard.json()["unpaid_amount"]) == 0

    notifications = await api.client.get("/api/v1/notifications", headers=api.tenant)
    notification_id = notifications.json()["items"][0]["id"]
    assert (
        await api.client.post(f"/api/v1/notifications/{notification_id}/read", headers=api.other)
    ).status_code == 404
    for _ in range(2):
        marked = await api.client.post(
            f"/api/v1/notifications/{notification_id}/read", headers=api.tenant
        )
        assert marked.status_code == 200
        assert marked.json()["count"] == 1
    marked_all = await api.client.post("/api/v1/notifications/read-all", headers=api.tenant)
    assert marked_all.json()["count"] == 3
    assert (await api.client.post("/api/v1/notifications/read-all", headers=api.tenant)).json()[
        "count"
    ] == 0
    assert (
        await api.client.get("/api/v1/notifications?unread_only=true", headers=api.tenant)
    ).json()["total"] == 0


async def test_manager_can_correct_rejected_reading_with_a_reason(rental: RentalContext) -> None:
    api = rental.api
    meter = await api.client.post(
        "/api/v1/meters",
        headers=api.manager,
        json={
            "room_id": rental.room_id,
            "kind": "water",
            "serial_number": "CORRECTION-WATER-001",
            "initial_reading": "100.000",
        },
    )
    assert meter.status_code == 201, meter.text
    reading = await api.client.post(
        f"/api/v1/meters/{meter.json()['id']}/readings",
        headers=api.tenant,
        json={"period": datetime.now(UTC).date().replace(day=1).isoformat(), "value": "120"},
    )
    assert reading.status_code == 201, reading.text
    reading_id = reading.json()["id"]
    rejected = await api.client.post(
        f"/api/v1/readings/{reading_id}/review",
        headers=api.manager,
        json={"status": "rejected", "reason": "Please verify"},
    )
    assert rejected.status_code == 200, rejected.text
    correction_path = f"/api/v1/readings/{reading_id}/correction"
    correction = {"value": "125", "reason": " Handover inspection "}
    assert (
        await api.client.post(correction_path, headers=api.tenant, json=correction)
    ).status_code == 403
    assert (
        await api.client.post(
            correction_path, headers=api.manager, json={"value": "125", "reason": " "}
        )
    ).status_code == 422
    corrected = await api.client.post(correction_path, headers=api.manager, json=correction)
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["status"] == "accepted"
    assert corrected.json()["submitted_by"] == api.tenant_id
    assert corrected.json()["correction_reason"] == "Handover inspection"
    assert corrected.json()["rejection_reason"] is None
    assert Decimal(corrected.json()["consumption"]) == Decimal("25")
    assert (
        await api.client.post(correction_path, headers=api.manager, json=correction)
    ).status_code == 409
    notices = await api.client.get("/api/v1/notifications?category=meters", headers=api.tenant)
    assert notices.status_code == 200
    assert notices.json()["total"] == 2
