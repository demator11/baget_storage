import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from baget_storage.core.exceptions import (
    AccessDenied,
    AuthenticationRequired,
    BusinessRuleViolation,
    Conflict,
    DomainError,
    EntityNotFound,
)

logger = logging.getLogger(__name__)


async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
    codes = {
        AuthenticationRequired: 401,
        AccessDenied: 403,
        EntityNotFound: 404,
        Conflict: 409,
        BusinessRuleViolation: 422,
    }
    status_code = codes.get(type(exc), 400)
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
        headers={"WWW-Authenticate": "Bearer"} if status_code == 401 else None,
    )


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"loc": list(item["loc"]), "type": item["type"], "message": "Invalid value"}
        for item in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "error": {"code": "validation_error", "message": "Invalid request", "details": details}
        },
    )


async def handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": "http_error", "message": str(exc.detail)}},
        headers=exc.headers,
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    error_id = str(uuid4())
    logger.error("Unhandled request error %s", error_id, exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": {"code": "internal_error", "message": "Internal server error", "id": error_id}
        },
    )


def setup_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, handle_domain_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(HTTPException, handle_http_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
