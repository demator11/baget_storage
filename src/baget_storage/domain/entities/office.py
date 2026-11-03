from dataclasses import dataclass
from enum import StrEnum


class TicketStatus(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    CLOSED = "closed"


class DocumentKind(StrEnum):
    CONTRACT = "contract"
    INVOICE = "invoice"
    ACT = "act"


class DocumentStatus(StrEnum):
    AVAILABLE = "available"
    ACTIVE = "active"
    SIGNED = "signed"


class NotificationCategory(StrEnum):
    SYSTEM = "system"
    PAYMENTS = "payments"
    DOCUMENTS = "documents"
    METERS = "meters"


@dataclass(frozen=True, slots=True)
class NotificationPreferences:
    notify_payments: bool = True
    notify_meters: bool = True
    notify_documents: bool = True

    def allows(self, category: NotificationCategory) -> bool:
        return {
            NotificationCategory.SYSTEM: True,
            NotificationCategory.PAYMENTS: self.notify_payments,
            NotificationCategory.METERS: self.notify_meters,
            NotificationCategory.DOCUMENTS: self.notify_documents,
        }[category]
