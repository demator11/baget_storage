from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = 11 * 1024 * 1024) -> None:
        if max_bytes < 1:
            raise ValueError("Request body limit must be positive")
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        expected_length: int | None = None
        for name, raw_value in scope.get("headers", []):
            if name.lower() != b"content-length":
                continue
            value = raw_value.strip()
            if not value.isdigit():
                await self._error(scope, receive, send, 400, "invalid_content_length")
                return
            significant = value.lstrip(b"0") or b"0"
            if len(significant) > len(str(self.max_bytes)):
                await self._error(scope, receive, send, 413, "request_too_large")
                return
            length = int(significant)
            if length > self.max_bytes:
                await self._error(scope, receive, send, 413, "request_too_large")
                return
            if expected_length is not None and expected_length != length:
                await self._error(scope, receive, send, 400, "invalid_content_length")
                return
            expected_length = length

        buffer = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(buffer) + len(chunk) > self.max_bytes:
                await self._error(scope, receive, send, 413, "request_too_large")
                return
            buffer.extend(chunk)
            if not message.get("more_body", False):
                break
        if expected_length is not None and expected_length != len(buffer):
            await self._error(scope, receive, send, 400, "invalid_content_length")
            return
        buffered_message: Message | None = {
            "type": "http.request",
            "body": bytes(buffer),
            "more_body": False,
        }
        buffer.clear()

        async def replay() -> Message:
            nonlocal buffered_message
            if buffered_message is not None:
                message = buffered_message
                buffered_message = None
                return message
            return await receive()

        await self.app(scope, replay, send)

    async def _error(
        self, scope: Scope, receive: Receive, send: Send, status_code: int, code: str
    ) -> None:
        message = "Request body is too large." if status_code == 413 else "Invalid Content-Length."
        response = JSONResponse(
            status_code=status_code,
            content={"error": {"code": code, "message": message}},
        )
        await response(scope, receive, send)
