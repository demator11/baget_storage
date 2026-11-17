from datetime import UTC, datetime
from io import BytesIO

from PIL import Image
from tests.api.test_workflows import api as api
from tests.api.test_workflows import rental as rental


async def test_meter_screen_summary_journal_photo_and_access(rental) -> None:
    api = rental.api
    client = api.client
    period = datetime.now(UTC).date().replace(day=1).isoformat()
    meters = []
    for serial in ("PHOTO-1", "PHOTO-2"):
        response = await client.post(
            "/api/v1/meters",
            headers=api.manager,
            json={
                "room_id": rental.room_id,
                "kind": "electricity",
                "serial_number": serial,
                "initial_reading": "100",
            },
        )
        assert response.status_code == 201, response.text
        meters.append(response.json()["id"])
    periods = await client.get("/api/v1/reading-periods", headers=api.tenant)
    assert periods.status_code == 200, periods.text
    assert periods.json()["items"][0]["period"] == period
    assert periods.json()["items"][0]["is_open"]
    submitted = await client.post(
        f"/api/v1/meters/{meters[0]}/readings",
        headers=api.tenant,
        json={"period": period, "value": "125"},
    )
    assert submitted.status_code == 201, submitted.text
    reading_id = submitted.json()["id"]
    photo = BytesIO()
    Image.new("RGB", (10, 10), "white").save(photo, format="PNG")
    uploaded = await client.put(
        f"/api/v1/readings/{reading_id}/photo",
        headers=api.tenant,
        files={"file": ("meter.png", photo.getvalue(), "image/png")},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["has_photo"]
    summary = await client.get(
        "/api/v1/meters/stats", headers=api.manager, params={"period": period}
    )
    assert summary.status_code == 200, summary.text
    assert summary.json() == {
        "period": period,
        "total": 2,
        "pending": 1,
        "accepted": 0,
        "rejected": 0,
        "missing": 1,
    }
    missing = await client.get(
        "/api/v1/meters", headers=api.manager, params={"period": period, "status": "missing"}
    )
    assert missing.status_code == 200, missing.text
    assert [item["id"] for item in missing.json()["items"]] == [meters[1]]
    journal = await client.get(
        "/api/v1/readings", headers=api.tenant, params={"room_id": rental.room_id}
    )
    assert journal.status_code == 200, journal.text
    row = journal.json()["items"][0]
    assert (row["room_name"], row["serial_number"], row["kind"], row["unit"]) == (
        "B2",
        "PHOTO-1",
        "electricity",
        "kwh",
    )
    assert row["has_photo"]
    invisible = await client.get("/api/v1/readings", headers=api.other)
    assert invisible.json()["total"] == 0
    forbidden = await client.get(f"/api/v1/readings/{reading_id}/photo", headers=api.other)
    assert forbidden.status_code == 403
    downloaded = await client.get(f"/api/v1/readings/{reading_id}/photo", headers=api.manager)
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"] == "image/jpeg"
    accepted = await client.post(
        f"/api/v1/readings/{reading_id}/review", headers=api.manager, json={"status": "accepted"}
    )
    assert accepted.status_code == 200, accepted.text
    locked_photo = await client.put(
        f"/api/v1/readings/{reading_id}/photo",
        headers=api.tenant,
        files={"file": ("meter.png", photo.getvalue(), "image/png")},
    )
    assert locked_photo.status_code == 409
