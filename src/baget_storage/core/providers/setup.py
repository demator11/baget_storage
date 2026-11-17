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
from baget_storage.domain.entities.auth import AuthPolicy
from baget_storage.domain.entities.email import EmailRetryPolicy
from baget_storage.domain.entities.reading_period import ReadingPeriodPolicy
from baget_storage.domain.ports.archive import ArchiveBuilder
from baget_storage.domain.ports.auth import AuthClock, AuthUnitOfWork, PasswordHasher, TokenService
from baget_storage.domain.ports.email import EmailDeliveryStore, EmailSender, WorkerClock
from baget_storage.domain.ports.health import HealthProbe
from baget_storage.domain.ports.meter import MeterClock, MeterPhotoProcessor, MeterUnitOfWork
from baget_storage.domain.ports.office import OfficeUnitOfWork
from baget_storage.domain.ports.reminders import ReminderUnitOfWork
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
from baget_storage.domain.use_cases.auth_sessions import GetSessionsUseCase, RefreshSessionUseCase
from baget_storage.domain.use_cases.bootstrap import CreateManagerUseCase, ResetPasswordUseCase
from baget_storage.domain.use_cases.email import DeliverEmailsUseCase
from baget_storage.domain.use_cases.email_auth import (
    CompletePasswordResetUseCase,
    ConfirmEmailUseCase,
    RequestPasswordResetUseCase,
    ResendVerificationUseCase,
)
from baget_storage.domain.use_cases.health import CheckHealthUseCase
from baget_storage.domain.use_cases.meter import (
    CorrectReadingUseCase,
    CreateMeterUseCase,
    GetMeterStatsUseCase,
    GetMetersUseCase,
    GetMeterUseCase,
    GetReadingJournalUseCase,
    GetReadingPeriodsUseCase,
    GetReadingsUseCase,
    GetReadingUseCase,
    ReviewReadingUseCase,
    SubmitReadingUseCase,
)
from baget_storage.domain.use_cases.meter_photo import (
    DownloadReadingPhotoUseCase,
    UploadReadingPhotoUseCase,
)
from baget_storage.domain.use_cases.notification_queries import (
    GetNotificationCountsUseCase,
    GetNotificationUseCase,
)
from baget_storage.domain.use_cases.office import (
    AttachInvoiceDocumentUseCase,
    CreateInvoiceUseCase,
    CreateNotificationUseCase,
    CreateTicketUseCase,
    DownloadDocumentUseCase,
    DownloadInvoiceUseCase,
    GetBillingSummaryUseCase,
    GetDashboardUseCase,
    GetDocumentStatsUseCase,
    GetDocumentsUseCase,
    GetDocumentUseCase,
    GetInvoicesUseCase,
    GetInvoiceUseCase,
    GetNotificationsUseCase,
    GetPaymentsUseCase,
    GetTicketHistoryUseCase,
    GetTicketStatsUseCase,
    GetTicketsUseCase,
    GetTicketUseCase,
    MarkNotificationsReadUseCase,
    RecordPaymentUseCase,
    TakeTicketUseCase,
    UpdateDocumentStatusUseCase,
    UpdateTicketUseCase,
    UploadDocumentUseCase,
)
from baget_storage.domain.use_cases.reminders import GenerateRemindersUseCase
from baget_storage.domain.use_cases.rental import (
    CreateLeaseUseCase,
    CreateRoomUseCase,
    GetExpiringLeasesUseCase,
    GetLeasesUseCase,
    GetLeaseUseCase,
    GetRoomStatsUseCase,
    GetRoomsUseCase,
    GetRoomUseCase,
    TerminateLeaseUseCase,
    UpdateRoomUseCase,
)
from baget_storage.infrastructure.archive import ZipArchiveBuilder
from baget_storage.infrastructure.clock import UtcMeterClock, UtcWorkerClock
from baget_storage.infrastructure.email import SmtpEmailSender
from baget_storage.infrastructure.health import PostgresHealthProbe
from baget_storage.infrastructure.meter_photo import PillowMeterPhotoProcessor
from baget_storage.infrastructure.postgres.auth_uow import AuthSqlAlchemyUnitOfWork
from baget_storage.infrastructure.postgres.database import create_engine, create_session_factory
from baget_storage.infrastructure.postgres.meter_uow import MeterSqlAlchemyUnitOfWork
from baget_storage.infrastructure.postgres.office_uow import OfficeSqlAlchemyUnitOfWork
from baget_storage.infrastructure.postgres.reminder_uow import ReminderSqlAlchemyUnitOfWork
from baget_storage.infrastructure.postgres.rental_uow import RentalSqlAlchemyUnitOfWork
from baget_storage.infrastructure.postgres.repository.email import SqlAlchemyEmailDeliveryStore
from baget_storage.infrastructure.security import (
    Argon2PasswordHasher,
    JwtTokenService,
    UtcAuthClock,
)


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
    def auth_policy(self, settings: Settings) -> AuthPolicy:
        return AuthPolicy(
            frontend_base_url=str(settings.frontend_base_url).rstrip("/"),
            refresh_days=settings.refresh_token_days,
            verification_hours=settings.email_verification_hours,
            password_reset_minutes=settings.password_reset_minutes,
        )

    @provide(scope=Scope.APP)
    def reading_policy(self, settings: Settings) -> ReadingPeriodPolicy:
        return ReadingPeriodPolicy(
            opening_day=settings.reading_opening_day, deadline_day=settings.reading_deadline_day
        )

    @provide(scope=Scope.APP)
    def email_retry_policy(self, settings: Settings) -> EmailRetryPolicy:
        return EmailRetryPolicy(
            max_attempts=settings.worker_max_attempts,
            claim_lease_seconds=max(300, settings.smtp_timeout_seconds * 12),
        )

    @provide(scope=Scope.APP)
    def email_sender(self, settings: Settings) -> EmailSender:
        return SmtpEmailSender(
            host=settings.smtp_host,
            port=settings.smtp_port,
            from_address=settings.smtp_from_address,
            frontend_base_url=str(settings.frontend_base_url),
            username=settings.smtp_username,
            password=settings.smtp_password.get_secret_value() if settings.smtp_password else None,
            starttls=settings.smtp_starttls,
            use_tls=settings.smtp_use_tls,
            timeout_seconds=settings.smtp_timeout_seconds,
        )

    @provide(scope=Scope.APP)
    def tokens(self, settings: Settings) -> TokenService:
        return JwtTokenService(
            settings.jwt_secret.get_secret_value(),
            settings.access_token_minutes,
            settings.jwt_issuer,
            settings.jwt_audience,
        )

    clock = provide(UtcMeterClock, provides=MeterClock, scope=Scope.APP)
    worker_clock = provide(UtcWorkerClock, provides=WorkerClock, scope=Scope.APP)
    email_store = provide(
        SqlAlchemyEmailDeliveryStore, provides=EmailDeliveryStore, scope=Scope.APP
    )
    reminder_uow = provide(
        ReminderSqlAlchemyUnitOfWork, provides=ReminderUnitOfWork, scope=Scope.REQUEST
    )
    auth_clock = provide(UtcAuthClock, provides=AuthClock, scope=Scope.APP)
    photos = provide(PillowMeterPhotoProcessor, provides=MeterPhotoProcessor, scope=Scope.APP)
    archives = provide(ZipArchiveBuilder, provides=ArchiveBuilder, scope=Scope.APP)
    health = provide(PostgresHealthProbe, provides=HealthProbe, scope=Scope.APP)
    auth_uow = provide(AuthSqlAlchemyUnitOfWork, provides=AuthUnitOfWork, scope=Scope.REQUEST)
    rental_uow = provide(RentalSqlAlchemyUnitOfWork, provides=RentalUnitOfWork, scope=Scope.REQUEST)
    meter_uow = provide(MeterSqlAlchemyUnitOfWork, provides=MeterUnitOfWork, scope=Scope.REQUEST)
    office_uow = provide(OfficeSqlAlchemyUnitOfWork, provides=OfficeUnitOfWork, scope=Scope.REQUEST)
    use_cases = provide_all(
        LoginUseCase,
        DeliverEmailsUseCase,
        GenerateRemindersUseCase,
        GetNotificationUseCase,
        GetNotificationCountsUseCase,
        RefreshSessionUseCase,
        GetSessionsUseCase,
        CompletePasswordResetUseCase,
        RequestPasswordResetUseCase,
        ConfirmEmailUseCase,
        ResendVerificationUseCase,
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
        GetLeaseUseCase,
        GetRoomStatsUseCase,
        GetExpiringLeasesUseCase,
        TerminateLeaseUseCase,
        CreateMeterUseCase,
        CorrectReadingUseCase,
        GetMetersUseCase,
        GetMeterUseCase,
        GetMeterStatsUseCase,
        GetReadingUseCase,
        GetReadingJournalUseCase,
        GetReadingPeriodsUseCase,
        DownloadReadingPhotoUseCase,
        UploadReadingPhotoUseCase,
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
        GetDocumentUseCase,
        GetInvoiceUseCase,
        UpdateDocumentStatusUseCase,
        TakeTicketUseCase,
        GetBillingSummaryUseCase,
        GetDocumentStatsUseCase,
        GetTicketUseCase,
        GetTicketStatsUseCase,
        scope=Scope.REQUEST,
    )


def create_container(settings: Settings) -> AsyncContainer:
    return make_async_container(ApplicationProvider(), context={Settings: settings})
