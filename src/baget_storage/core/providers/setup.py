from collections.abc import AsyncIterator

from dishka import (
    AsyncContainer,
    Provider,
    Scope,
    from_context,
    make_async_container,
    provide,
    provide_all,
)
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from baget_storage.core.config import Settings
from baget_storage.domain.ports.archive import ArchiveBuilder
from baget_storage.domain.ports.auth import AuthUnitOfWork, PasswordHasher, TokenService
from baget_storage.domain.ports.health import HealthProbe
from baget_storage.domain.ports.meter import MeterClock, MeterUnitOfWork
from baget_storage.domain.ports.office import OfficeUnitOfWork
from baget_storage.domain.ports.rental import RentalUnitOfWork
from baget_storage.domain.use_cases.archive import DownloadDocumentsArchiveUseCase
from baget_storage.domain.use_cases.auth import (
    AuthenticateUserUseCase,
    ChangePasswordUseCase,
    CreateUserUseCase,
    GetProfileUseCase,
    ListUsersUseCase,
    LoginUseCase,
    LogoutUseCase,
    UpdateProfileUseCase,
)
from baget_storage.domain.use_cases.bootstrap import CreateManagerUseCase, ResetPasswordUseCase
from baget_storage.domain.use_cases.health import CheckHealthUseCase
from baget_storage.domain.use_cases.meter import (
    CorrectReadingUseCase,
    CreateMeterUseCase,
    GetMetersUseCase,
    GetReadingsUseCase,
    ReviewReadingUseCase,
    SubmitReadingUseCase,
)
from baget_storage.domain.use_cases.office import (
    AttachInvoiceDocumentUseCase,
    CreateInvoiceUseCase,
    CreateNotificationUseCase,
    CreateTicketUseCase,
    DownloadDocumentUseCase,
    DownloadInvoiceUseCase,
    GetDashboardUseCase,
    GetDocumentsUseCase,
    GetInvoicesUseCase,
    GetNotificationsUseCase,
    GetPaymentsUseCase,
    GetTicketHistoryUseCase,
    GetTicketsUseCase,
    MarkNotificationsReadUseCase,
    RecordPaymentUseCase,
    UpdateTicketUseCase,
    UploadDocumentUseCase,
)
from baget_storage.domain.use_cases.rental import (
    CreateLeaseUseCase,
    CreateRoomUseCase,
    GetLeasesUseCase,
    GetRoomsUseCase,
    GetRoomUseCase,
    TerminateLeaseUseCase,
    UpdateRoomUseCase,
)
from baget_storage.infrastructure.archive import ZipArchiveBuilder
from baget_storage.infrastructure.clock import UtcMeterClock
from baget_storage.infrastructure.health import PostgresHealthProbe
from baget_storage.infrastructure.postgres.auth_uow import AuthSqlAlchemyUnitOfWork
from baget_storage.infrastructure.postgres.database import create_engine, create_session_factory
from baget_storage.infrastructure.postgres.meter_uow import MeterSqlAlchemyUnitOfWork
from baget_storage.infrastructure.postgres.office_uow import OfficeSqlAlchemyUnitOfWork
from baget_storage.infrastructure.postgres.rental_uow import RentalSqlAlchemyUnitOfWork
from baget_storage.infrastructure.security import Argon2PasswordHasher, JwtTokenService


class ApplicationProvider(Provider):
    settings = from_context(provides=Settings, scope=Scope.APP)

    @provide(scope=Scope.APP)
    async def engine(self, settings: Settings) -> AsyncIterator[AsyncEngine]:
        engine = create_engine(settings.database_url)
        try:
            yield engine
        finally:
            await engine.dispose()

    @provide(scope=Scope.APP)
    def sessions(self, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
        return create_session_factory(engine)

    @provide(scope=Scope.APP)
    def passwords(self) -> PasswordHasher:
        return Argon2PasswordHasher()

    @provide(scope=Scope.APP)
    def tokens(self, settings: Settings) -> TokenService:
        return JwtTokenService(
            settings.jwt_secret.get_secret_value(),
            settings.access_token_minutes,
            settings.jwt_issuer,
            settings.jwt_audience,
        )

    clock = provide(UtcMeterClock, provides=MeterClock, scope=Scope.APP)
    archives = provide(ZipArchiveBuilder, provides=ArchiveBuilder, scope=Scope.APP)
    health = provide(PostgresHealthProbe, provides=HealthProbe, scope=Scope.APP)
    auth_uow = provide(AuthSqlAlchemyUnitOfWork, provides=AuthUnitOfWork, scope=Scope.REQUEST)
    rental_uow = provide(RentalSqlAlchemyUnitOfWork, provides=RentalUnitOfWork, scope=Scope.REQUEST)
    meter_uow = provide(MeterSqlAlchemyUnitOfWork, provides=MeterUnitOfWork, scope=Scope.REQUEST)
    office_uow = provide(OfficeSqlAlchemyUnitOfWork, provides=OfficeUnitOfWork, scope=Scope.REQUEST)
    use_cases = provide_all(
        LoginUseCase,
        DownloadDocumentsArchiveUseCase,
        CreateManagerUseCase,
        ResetPasswordUseCase,
        LogoutUseCase,
        AuthenticateUserUseCase,
        GetProfileUseCase,
        UpdateProfileUseCase,
        ChangePasswordUseCase,
        CreateUserUseCase,
        ListUsersUseCase,
        CheckHealthUseCase,
        CreateRoomUseCase,
        GetRoomUseCase,
        GetRoomsUseCase,
        UpdateRoomUseCase,
        CreateLeaseUseCase,
        GetLeasesUseCase,
        TerminateLeaseUseCase,
        CreateMeterUseCase,
        CorrectReadingUseCase,
        GetMetersUseCase,
        GetReadingsUseCase,
        SubmitReadingUseCase,
        ReviewReadingUseCase,
        CreateTicketUseCase,
        GetTicketsUseCase,
        UpdateTicketUseCase,
        GetTicketHistoryUseCase,
        CreateInvoiceUseCase,
        AttachInvoiceDocumentUseCase,
        DownloadInvoiceUseCase,
        GetInvoicesUseCase,
        RecordPaymentUseCase,
        GetPaymentsUseCase,
        UploadDocumentUseCase,
        GetDocumentsUseCase,
        DownloadDocumentUseCase,
        GetNotificationsUseCase,
        MarkNotificationsReadUseCase,
        CreateNotificationUseCase,
        GetDashboardUseCase,
        scope=Scope.REQUEST,
    )


def create_container(settings: Settings) -> AsyncContainer:
    return make_async_container(ApplicationProvider(), context={Settings: settings})
