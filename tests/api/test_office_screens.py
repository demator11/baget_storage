import io
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.api.test_workflows import RentalContext
from tests.api.test_workflows import api as api
from tests.api.test_workflows import rental as rental

pytestmark = pytest.mark.integration


async def test_room_ticket_and_billing_screen_routes(rental: RentalContext) -> None:
    context = rental.api
    client = context.client
    today = datetime.now(UTC).date()
    period = today.replace(day=1).isoformat()

    room = await client.get(f"/api/v1/rooms/{rental.room_id}", headers=context.tenant)
    assert room.status_code == 200, room.text
    assert room.json()["active_lease"]["id"] == rental.lease_id
    assert room.json()["active_lease"]["tenant"]["company_name"] == "Company"
    rooms = await client.get("/api/v1/rooms/stats", headers=context.tenant)
    assert rooms.status_code == 200, rooms.text
    assert rooms.json()["total"] == rooms.json()["occupied"] == 1
    assert Decimal(rooms.json()["total_area_m2"]) == Decimal("350")
    assert (await client.get("/api/v1/rooms/stats", headers=context.other)).json()["total"] == 0
    expiring = await client.get("/api/v1/leases/expiring?within_days=366", headers=context.tenant)
    assert expiring.status_code == 200, expiring.text
    assert [item["id"] for item in expiring.json()["items"]] == [rental.lease_id]
    assert (
        await client.get("/api/v1/leases/expiring?within_days=366", headers=context.other)
    ).json()["total"] == 0
    lease = await client.get(f"/api/v1/leases/{rental.lease_id}", headers=context.tenant)
    assert lease.status_code == 200, lease.text
    assert lease.json()["room"]["name"] == "B2"
    assert lease.json()["tenant"]["id"] == context.tenant_id
    assert (
        await client.get(f"/api/v1/leases/{rental.lease_id}", headers=context.other)
    ).status_code == 404

    ticket = await client.post(
        "/api/v1/tickets",
        headers=context.tenant,
        json={"room_id": rental.room_id, "title": "Screen repair", "description": "Gate is stuck"},
    )
    assert ticket.status_code == 201, ticket.text
    ticket_id = ticket.json()["id"]
    assert ticket.json()["number"].startswith("REQ-")
    assert ticket.json()["room"]["id"] == rental.room_id
    assert ticket.json()["creator"]["full_name"] == "tenant"
    detail = await client.get(f"/api/v1/tickets/{ticket_id}", headers=context.tenant)
    assert detail.status_code == 200, detail.text
    assert detail.json()["tenant"]["id"] == context.tenant_id
    assert (
        await client.get(f"/api/v1/tickets/{ticket_id}", headers=context.other)
    ).status_code == 404
    history = await client.get(f"/api/v1/tickets/{ticket_id}/history", headers=context.tenant)
    assert history.status_code == 200, history.text
    assert history.json()["items"][0]["actor"]["full_name"] == "tenant"
    cards = await client.get(f"/api/v1/tickets/stats?period={period}", headers=context.tenant)
    assert cards.status_code == 200, cards.text
    assert cards.json()["total"] == cards.json()["new"] == 1
    assert cards.json()["period"] == period
    assert (await client.get("/api/v1/tickets/stats", headers=context.other)).json()["total"] == 0
    invalid_period = await client.get(
        "/api/v1/tickets/stats?period=2026-11-02", headers=context.tenant
    )
    assert invalid_period.status_code == 422

    take_path = f"/api/v1/tickets/{ticket_id}/take"
    assert (await client.post(take_path, headers=context.tenant)).status_code == 403
    taken = await client.post(take_path, headers=context.manager)
    assert taken.status_code == 200, taken.text
    assert taken.json()["status"] == "in_progress"
    assert taken.json()["assignee"] == "Test Manager"
    assert (await client.post(take_path, headers=context.manager)).status_code == 409
    history = await client.get(f"/api/v1/tickets/{ticket_id}/history", headers=context.tenant)
    assert history.json()["total"] == 2
    assert history.json()["items"][-1]["actor"]["full_name"] == "Test Manager"

    invoice = await client.post(
        "/api/v1/invoices",
        headers=context.manager,
        json={
            "lease_id": rental.lease_id,
            "number": "SCREEN-HTTP-I1",
            "description": "Monthly rent",
            "amount": "100.00",
            "issued_on": today.isoformat(),
            "due_date": (today + timedelta(days=1)).isoformat(),
        },
    )
    assert invoice.status_code == 201, invoice.text
    invoice_id = invoice.json()["id"]
    assert invoice.json()["lease"]["id"] == rental.lease_id
    assert invoice.json()["room"]["name"] == "B2"
    detail = await client.get(f"/api/v1/invoices/{invoice_id}", headers=context.tenant)
    assert detail.status_code == 200, detail.text
    assert detail.json()["tenant"]["id"] == context.tenant_id
    assert (
        await client.get(f"/api/v1/invoices/{invoice_id}", headers=context.other)
    ).status_code == 404
    payment = await client.post(
        f"/api/v1/invoices/{invoice_id}/payments",
        headers=context.manager,
        json={"amount": "40.00", "reference": "SCREEN-HTTP-P1", "paid_on": today.isoformat()},
    )
    assert payment.status_code == 201, payment.text
    assert payment.json()["invoice"]["number"] == "SCREEN-HTTP-I1"
    assert payment.json()["room"]["id"] == rental.room_id
    summary = await client.get("/api/v1/billing/summary", headers=context.tenant)
    assert summary.status_code == 200, summary.text
    assert summary.json()["due_count"] == 1
    assert Decimal(summary.json()["unpaid_amount"]) == Decimal("60")
    assert Decimal(summary.json()["paid_current_month"]) == Decimal("40")
    assert summary.json()["next_unpaid_invoice"]["id"] == invoice_id
    assert summary.json()["last_payment"]["id"] == payment.json()["id"]
    empty_summary = await client.get("/api/v1/billing/summary", headers=context.other)
    assert empty_summary.status_code == 200, empty_summary.text
    assert empty_summary.json()["due_count"] == 0
    assert empty_summary.json()["next_unpaid_invoice"] is None
    dashboard = await client.get("/api/v1/dashboard", headers=context.tenant)
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["next_payment"]["id"] == invoice_id
    assert dashboard.json()["room_metrics"]["occupied"] == 1


async def test_document_status_detail_archive_and_notifications(rental: RentalContext) -> None:
    context = rental.api
    client = context.client
    today = datetime.now(UTC).date().isoformat()
    payloads = {"contract": b"%PDF-1.7\ncontract", "act": b"%PDF-1.7\nact"}
    documents = {}
    for kind, content in payloads.items():
        response = await client.post(
            "/api/v1/documents",
            headers=context.manager,
            data={"lease_id": rental.lease_id, "title": kind, "kind": kind, "issued_on": today},
            files={"file": (f"{kind}.pdf", content, "application/pdf")},
        )
        assert response.status_code == 201, response.text
        documents[kind] = response.json()
    contract_id = documents["contract"]["id"]
    detail = await client.get(f"/api/v1/documents/{contract_id}", headers=context.tenant)
    assert detail.status_code == 200, detail.text
    assert detail.json()["room"]["name"] == "B2"
    assert detail.json()["lease"]["number"] == "LEASE-001"
    assert detail.json()["tenant"]["id"] == context.tenant_id
    assert (
        await client.get(f"/api/v1/documents/{contract_id}", headers=context.other)
    ).status_code == 404
    path = f"/api/v1/documents/{contract_id}/status"
    assert (
        await client.patch(path, headers=context.tenant, json={"status": "signed"})
    ).status_code == 403
    baseline = await client.get("/api/v1/notifications?category=documents", headers=context.tenant)
    assert baseline.status_code == 200, baseline.text
    updated = await client.patch(path, headers=context.manager, json={"status": "signed"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "signed"
    repeated = await client.patch(path, headers=context.manager, json={"status": "signed"})
    assert repeated.status_code == 200, repeated.text
    notices = await client.get("/api/v1/notifications?category=documents", headers=context.tenant)
    assert notices.json()["total"] == baseline.json()["total"] + 1
    assert any(
        item["resource_type"] == "document" and item["resource_id"] == contract_id
        for item in notices.json()["items"]
    )
    cards = await client.get("/api/v1/documents/stats", headers=context.tenant)
    assert cards.status_code == 200, cards.text
    assert cards.json()["total"] == 2
    assert cards.json()["contracts"] == cards.json()["acts"] == 1
    assert cards.json()["signed"] == cards.json()["available"] == 1
    assert (await client.get("/api/v1/documents/stats", headers=context.other)).json()["total"] == 0
    archive = await client.get("/api/v1/documents/archive?status=signed", headers=context.tenant)
    assert archive.status_code == 200, archive.text
    assert archive.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(archive.content)) as result:
        assert result.namelist() == [f"{contract_id}-contract.pdf"]
        assert result.read(result.namelist()[0]) == payloads["contract"]
    foreign_archive = await client.get(
        "/api/v1/documents/archive?status=signed", headers=context.other
    )
    assert foreign_archive.status_code == 200, foreign_archive.text
    with zipfile.ZipFile(io.BytesIO(foreign_archive.content)) as result:
        assert result.namelist() == []
    invalid = await client.patch(
        f"/api/v1/documents/{documents['act']['id']}/status",
        headers=context.manager,
        json={"status": "active"},
    )
    assert invalid.status_code == 422
