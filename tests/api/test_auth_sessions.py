import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from baget_storage.app import create_app
from baget_storage.core.config import Settings
from baget_storage.domain.entities.user import Role
from baget_storage.infrastructure.postgres.auth_uow import AuthSqlAlchemyUnitOfWork
from baget_storage.infrastructure.postgres.models.auth import (
    AuthSessionOrm,
    EmailActionTokenOrm,
    RefreshTokenOrm,
)
from baget_storage.infrastructure.postgres.models.email import EmailOutboxOrm
from baget_storage.infrastructure.security import Argon2PasswordHasher
from baget_storage.schemas.user import StoreUser

pytestmark = pytest.mark.integration
PASSWORD = "session-test-password-123"


@pytest.fixture
async def auth_client(
    session_factory: async_sessionmaker[AsyncSession], test_database_url: str
) -> AsyncIterator[httpx.AsyncClient]:
    password_hash = await Argon2PasswordHasher().hash(PASSWORD)
    async with AuthSqlAlchemyUnitOfWork(session_factory) as uow:
        for role in (Role.TENANT, Role.MANAGER):
            await uow.users.create(
                StoreUser(
                    email=f"{role.value}@example.com",
                    full_name=role.value,
                    password_hash=password_hash,
                    role=role,
                )
            )
        await uow.commit()
    app = create_app(
        Settings(
            database_url=test_database_url,
            jwt_secret="auth-api-test-secret-" * 3,
            environment="test",
            frontend_base_url="https://frontend.example.com",
        )
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client


async def sign_in(
    client: httpx.AsyncClient, role: str = "tenant", password: str = PASSWORD
) -> dict:
    response = await client.post(
        "/api/v1/auth/login", json={"email": f"{role}@example.com", "password": password}
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def headers(session: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {session['access_token']}"}


async def outbox_token(session_factory: async_sessionmaker[AsyncSession], template: str) -> str:
    async with session_factory() as session:
        row = await session.scalar(
            select(EmailOutboxOrm)
            .where(EmailOutboxOrm.template == template)
            .order_by(EmailOutboxOrm.created_at.desc(), EmailOutboxOrm.id)
            .limit(1)
        )
        assert row is not None
        return parse_qs(urlparse(row.payload["url"]).query)["token"][0]


async def test_persisted_sessions_refresh_rotation_and_current_logout(
    auth_client, session_factory
) -> None:
    first = await sign_in(auth_client)
    second = await sign_in(auth_client)
    assert {
        "access_token",
        "token_type",
        "expires_in",
        "refresh_token",
        "refresh_expires_in",
        "session_id",
        "user",
    } <= first.keys()
    assert first["session_id"] != second["session_id"]
    sessions = await auth_client.get("/api/v1/auth/sessions", headers=headers(first))
    assert sessions.status_code == 200, sessions.text
    assert len(sessions.json()) == 2
    assert sum(row["is_current"] for row in sessions.json()) == 1
    rotated = await auth_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["session_id"] == first["session_id"]
    assert rotated.json()["refresh_token"] != first["refresh_token"]
    async with session_factory() as session:
        tokens = list(await session.scalars(select(RefreshTokenOrm)))
        assert len(tokens) == 3
        assert all(row.token_hash != first["refresh_token"] for row in tokens)
        assert sum(row.consumed_at is not None for row in tokens) == 1
    assert (
        await auth_client.post("/api/v1/auth/logout", headers=headers(rotated.json()))
    ).status_code == 204
    assert (await auth_client.get("/api/v1/users/me", headers=headers(first))).status_code == 401
    assert (
        await auth_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": rotated.json()["refresh_token"]}
        )
    ).status_code == 401
    assert (await auth_client.get("/api/v1/users/me", headers=headers(second))).status_code == 200


async def test_concurrent_refresh_replay_revokes_entire_session_family(
    auth_client, session_factory
) -> None:
    original = await sign_in(auth_client)
    results = await asyncio.gather(
        *(
            auth_client.post(
                "/api/v1/auth/refresh", json={"refresh_token": original["refresh_token"]}
            )
            for _ in range(2)
        )
    )
    assert sorted(response.status_code for response in results) == [200, 401]
    issued = next(response.json() for response in results if response.status_code == 200)
    assert (await auth_client.get("/api/v1/users/me", headers=headers(issued))).status_code == 401
    assert (
        await auth_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": issued["refresh_token"]}
        )
    ).status_code == 401
    async with session_factory() as session:
        row = await session.scalar(select(AuthSessionOrm))
        assert row is not None and row.revoked_at is not None


async def test_expired_server_session_rejects_unexpired_access_token(
    auth_client, session_factory
) -> None:
    current = await sign_in(auth_client)
    async with session_factory.begin() as session:
        await session.execute(
            update(AuthSessionOrm).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert (await auth_client.get("/api/v1/users/me", headers=headers(current))).status_code == 401
    assert (
        await auth_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": current["refresh_token"]}
        )
    ).status_code == 401


async def test_email_verification_single_use_and_password_reset_revokes_sessions(
    auth_client, session_factory
) -> None:
    current = await sign_in(auth_client)
    second = await sign_in(auth_client)
    assert current["user"]["email_verified_at"] is None
    sent = await auth_client.post(
        "/api/v1/auth/email-verification/resend", headers=headers(current)
    )
    assert sent.status_code == 202, sent.text
    token = await outbox_token(session_factory, "verify_email")
    async with session_factory() as session:
        stored = await session.scalar(select(EmailActionTokenOrm))
        assert stored is not None and stored.token_hash != token
    confirmed = await auth_client.post(
        "/api/v1/auth/email-verification/confirm", json={"token": token}
    )
    assert confirmed.status_code == 204, confirmed.text
    assert (
        await auth_client.post("/api/v1/auth/email-verification/confirm", json={"token": token})
    ).status_code == 401
    profile = await auth_client.get("/api/v1/users/me", headers=headers(current))
    assert profile.json()["email_verified_at"] is not None
    known = await auth_client.post(
        "/api/v1/auth/password-reset/request", json={"email": "tenant@example.com"}
    )
    unknown = await auth_client.post(
        "/api/v1/auth/password-reset/request", json={"email": "unknown@example.com"}
    )
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    reset = await outbox_token(session_factory, "password_reset")
    completed = await auth_client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": reset, "new_password": "new-password-for-api-123"},
    )
    assert completed.status_code == 204, completed.text
    for old in (current, second):
        assert (await auth_client.get("/api/v1/users/me", headers=headers(old))).status_code == 401
        assert (
            await auth_client.post(
                "/api/v1/auth/refresh", json={"refresh_token": old["refresh_token"]}
            )
        ).status_code == 401
    assert (
        await auth_client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": reset, "new_password": "different-password-123"},
        )
    ).status_code == 401
    assert (await sign_in(auth_client, password="new-password-for-api-123"))["user"][
        "email_verified_at"
    ] is not None


async def test_role_specific_profile_preferences(auth_client) -> None:
    manager = await sign_in(auth_client, "manager")
    tenant = await sign_in(auth_client)
    allowed = await auth_client.patch(
        "/api/v1/users/me",
        headers=headers(manager),
        json={"notify_meters": False, "notify_tickets": False, "notify_leases": False},
    )
    assert allowed.status_code == 200, allowed.text
    assert not allowed.json()["notify_tickets"]
    assert not allowed.json()["notify_leases"]
    assert allowed.json()["accessible_buildings"] == []
    assert (
        await auth_client.patch(
            "/api/v1/users/me", headers=headers(manager), json={"notify_payments": False}
        )
    ).status_code == 403
    assert (
        await auth_client.patch(
            "/api/v1/users/me", headers=headers(tenant), json={"notify_tickets": False}
        )
    ).status_code == 403
    assert (
        await auth_client.patch(
            "/api/v1/users/me", headers=headers(tenant), json={"notify_documents": False}
        )
    ).status_code == 200
