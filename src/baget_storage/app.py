from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from baget_storage.api.archive import router as archive_router
from baget_storage.api.auth import router as auth_router
from baget_storage.api.exception_handlers import setup_exception_handlers
from baget_storage.api.health import router as health_router
from baget_storage.api.meters import router as meters_router
from baget_storage.api.middleware import RequestBodyLimitMiddleware
from baget_storage.api.notifications import router as notifications_router
from baget_storage.api.office import router as office_router
from baget_storage.api.rental import router as rental_router
from baget_storage.core.config import Settings
from baget_storage.core.providers.setup import create_container


def create_app(settings: Settings | None = None) -> FastAPI:
    configuration = settings or Settings()
    container = create_container(configuration)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await container.close()

    app = FastAPI(title=configuration.app_name, version="0.1.0", lifespan=lifespan)
    setup_exception_handlers(app)
    setup_dishka(container, app)
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=11 * 1024 * 1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=configuration.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "PUT"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["Content-Disposition"],
    )
    app.include_router(health_router)
    for router in (
        auth_router,
        rental_router,
        meters_router,
        archive_router,
        office_router,
        notifications_router,
    ):
        app.include_router(router, prefix="/api/v1")
    return app
