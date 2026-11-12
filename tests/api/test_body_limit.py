from collections.abc import AsyncIterator
from typing import Annotated
from uuid import uuid4

import pytest
from fastapi import FastAPI, Form
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Message, Receive, Scope, Send

from baget_storage.api.auth import get_actor
from baget_storage.api.exception_handlers import setup_exception_handlers
from baget_storage.api.middleware import RequestBodyLimitMiddleware
from baget_storage.api.office import router as office_router
from baget_storage.app import create_app
from baget_storage.core.config import Settings
from baget_storage.domain.entities.user import Actor, Role
from baget_storage.schemas.api.office import DocumentTitle


class BodyRecorder:
    def __init__(self) -> None:
        self.bodies: list[bytes] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        body = await Request(scope, receive).body()
        self.bodies.append(body)
        await JSONResponse({"size": len(body), "content": body.hex()})(scope, receive, send)


async def test_known_length_body_at_limit_reaches_application_unchanged() -> None:
    recorder = BodyRecorder()
    middleware = RequestBodyLimitMiddleware(recorder, max_bytes=8)
    async with AsyncClient(
        transport=ASGITransport(app=middleware), base_url="http://test"
    ) as client:
        response = await client.post("/", content=b"12345678")
    assert response.status_code == 200
    assert response.json() == {"size": 8, "content": b"12345678".hex()}
    assert recorder.bodies == [b"12345678"]


async def test_chunked_body_over_limit_stops_without_draining_or_calling_application() -> None:
    recorder = BodyRecorder()
    middleware = RequestBodyLimitMiddleware(recorder, max_bytes=8)
    consumed: list[bytes] = []

    async def chunks() -> AsyncIterator[bytes]:
        for chunk in (b"1234", b"56789", b"unread tail"):
            consumed.append(chunk)
            yield chunk

    async with AsyncClient(
        transport=ASGITransport(app=middleware), base_url="http://test"
    ) as client:
        response = await client.post("/", content=chunks())
    assert response.status_code == 413
    assert response.json() == {
        "error": {"code": "request_too_large", "message": "Request body is too large."}
    }
    assert consumed == [b"1234", b"56789"]
    assert recorder.bodies == []


async def test_chunked_body_within_limit_is_replayed() -> None:
    recorder = BodyRecorder()
    middleware = RequestBodyLimitMiddleware(recorder, max_bytes=8)

    async def chunks() -> AsyncIterator[bytes]:
        for chunk in (b"12", b"", b"345", b"678"):
            yield chunk

    async with AsyncClient(
        transport=ASGITransport(app=middleware), base_url="http://test"
    ) as client:
        response = await client.post("/", content=chunks())
    assert response.status_code == 200
    assert recorder.bodies == [b"12345678"]


async def test_oversized_advertised_length_does_not_read_any_body() -> None:
    recorder = BodyRecorder()
    middleware = RequestBodyLimitMiddleware(recorder, max_bytes=8)
    consumed = []

    async def chunks() -> AsyncIterator[bytes]:
        consumed.append(True)
        yield b"123456789"

    async with AsyncClient(
        transport=ASGITransport(app=middleware), base_url="http://test"
    ) as client:
        response = await client.post("/", content=chunks(), headers={"Content-Length": "9"})
    assert response.status_code == 413
    assert consumed == []
    assert recorder.bodies == []


async def test_small_advertised_length_cannot_hide_an_oversized_body() -> None:
    recorder = BodyRecorder()
    middleware = RequestBodyLimitMiddleware(recorder, max_bytes=8)
    async with AsyncClient(
        transport=ASGITransport(app=middleware), base_url="http://test"
    ) as client:
        response = await client.post("/", content=b"123456789", headers={"Content-Length": "1"})
    assert response.status_code == 413
    assert recorder.bodies == []


@pytest.mark.parametrize("value", ["-1", "nonsense", "1.2", "+1", "", "4, 5"])
async def test_invalid_content_length_is_rejected(value: str) -> None:
    recorder = BodyRecorder()
    middleware = RequestBodyLimitMiddleware(recorder, max_bytes=8)
    async with AsyncClient(
        transport=ASGITransport(app=middleware), base_url="http://test"
    ) as client:
        response = await client.post("/", content=b"1234", headers={"Content-Length": value})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_content_length"
    assert recorder.bodies == []


async def test_mismatching_duplicate_lengths_are_rejected() -> None:
    recorder = BodyRecorder()
    middleware = RequestBodyLimitMiddleware(recorder, max_bytes=8)
    async with AsyncClient(
        transport=ASGITransport(app=middleware), base_url="http://test"
    ) as client:
        response = await client.post(
            "/", content=b"1234", headers=[("Content-Length", "4"), ("Content-Length", "5")]
        )
    assert response.status_code == 400
    assert recorder.bodies == []


async def test_length_must_match_actual_received_body() -> None:
    recorder = BodyRecorder()
    middleware = RequestBodyLimitMiddleware(recorder, max_bytes=8)
    async with AsyncClient(
        transport=ASGITransport(app=middleware), base_url="http://test"
    ) as client:
        response = await client.post("/", content=b"1234", headers={"Content-Length": "5"})
    assert response.status_code == 400
    assert recorder.bodies == []


async def test_non_http_scope_is_forwarded_without_reading() -> None:
    observed: list[Scope] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        observed.append(scope)

    async def receive() -> Message:
        raise AssertionError("The middleware must not consume non-HTTP messages")

    async def send(message: Message) -> None:
        raise AssertionError("The middleware must not emit non-HTTP messages")

    middleware = RequestBodyLimitMiddleware(downstream, max_bytes=8)
    scope = {"type": "websocket"}
    await middleware(scope, receive, send)
    assert observed == [scope]


async def test_disconnected_client_never_reaches_downstream() -> None:
    recorder = BodyRecorder()
    middleware = RequestBodyLimitMiddleware(recorder, max_bytes=8)

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        raise AssertionError("No response can be sent after disconnect")

    await middleware({"type": "http", "headers": []}, receive, send)
    assert recorder.bodies == []


async def test_document_form_title_rejects_whitespace_at_transport_boundary() -> None:
    app = FastAPI()
    seen: list[str] = []

    @app.post("/document")
    async def document(title: Annotated[DocumentTitle, Form()]) -> dict[str, str]:
        seen.append(title)
        return {"title": title}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        invalid = await client.post("/document", data={"title": "   "})
        valid = await client.post("/document", data={"title": "  Contract  "})
    assert invalid.status_code == 422
    assert valid.status_code == 200
    assert valid.json() == {"title": "Contract"}
    assert seen == ["Contract"]


async def test_upload_route_returns_422_for_whitespace_title_with_authenticated_manager() -> None:
    app = FastAPI()
    setup_exception_handlers(app)
    app.include_router(office_router, prefix="/api/v1")

    async def authenticated_manager() -> Actor:
        return Actor(id=uuid4(), role=Role.MANAGER)

    app.dependency_overrides[get_actor] = authenticated_manager
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/documents",
            data={
                "lease_id": str(uuid4()),
                "title": "   ",
                "kind": "contract",
                "issued_on": "2026-11-17",
            },
            files={"file": ("contract.pdf", b"%PDF-1.4 content", "application/pdf")},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["details"][0]["loc"] == ["body", "title"]


async def test_configured_application_limits_uploads_before_auth_and_preserves_cors() -> None:
    app = create_app(
        Settings(
            _env_file=None,
            environment="test",
            jwt_secret="request-limit-test-secret-value-32",
            database_url="postgresql+asyncpg://baget@127.0.0.1:1/body_limit_test",
            cors_origins=["https://client.example"],
        )
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/documents",
                content=b"",
                headers={
                    "Content-Length": str(11 * 1024 * 1024 + 1),
                    "Origin": "https://client.example",
                },
            )
    assert response.status_code == 413
    assert response.headers["access-control-allow-origin"] == "https://client.example"
