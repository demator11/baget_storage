import asyncio
from collections.abc import AsyncIterator
from email import policy
from email.parser import BytesParser
from uuid import uuid4

import pytest

from baget_storage.domain.entities.email import EmailDeliveryError
from baget_storage.infrastructure.email import SmtpEmailSender
from baget_storage.schemas.email import EmailJob


@pytest.fixture
async def smtp_stub() -> AsyncIterator[tuple[int, list[bytes]]]:
    received: list[bytes] = []

    async def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"220 localhost test SMTP\r\n")
        await writer.drain()
        try:
            while line := await reader.readline():
                command = line.split(b" ", 1)[0].strip().upper()
                if command == b"DATA":
                    writer.write(b"354 End with dot\r\n")
                    await writer.drain()
                    received.append(await reader.readuntil(b"\r\n.\r\n"))
                    writer.write(b"250 Accepted\r\n")
                elif command == b"QUIT":
                    writer.write(b"221 Bye\r\n")
                    await writer.drain()
                    break
                else:
                    writer.write(b"250 OK\r\n")
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1], received
    finally:
        server.close()
        await server.wait_closed()


async def test_real_smtp_adapter_sends_only_to_local_stub_with_stable_message_id(smtp_stub):
    port, received = smtp_stub
    sender = SmtpEmailSender(
        host="127.0.0.1",
        port=port,
        from_address="noreply@example.com",
        frontend_base_url="https://app.example.com",
        starttls=False,
    )
    job = EmailJob(
        id=uuid4(),
        recipient="tenant@example.com",
        template="password_reset",
        payload={"url": "https://app.example.com/reset?token=test-token"},
        attempts=1,
        claim_id=uuid4(),
        expires_at=None,
    )
    await sender.send(job)
    await sender.send(job.model_copy(update={"attempts": 2}))
    assert len(received) == 2
    messages = [BytesParser(policy=policy.default).parsebytes(raw) for raw in received]
    assert messages[0]["To"] == "tenant@example.com"
    assert messages[0]["Message-ID"] == messages[1]["Message-ID"]
    assert "https://app.example.com/reset?token=test-token" in messages[0].get_content()


async def test_invalid_payload_is_permanent_without_network_io():
    sender = SmtpEmailSender(
        "127.0.0.1", 1, "noreply@example.com", "https://app.example.com", starttls=False
    )
    job = EmailJob(
        id=uuid4(),
        recipient="tenant@example.com",
        template="verify_email",
        payload={"url": "javascript:alert(1)"},
        attempts=1,
        claim_id=uuid4(),
        expires_at=None,
    )
    with pytest.raises(EmailDeliveryError) as caught:
        await sender.send(job)
    assert caught.value.permanent
    assert caught.value.code == "invalid_email_link"
