from dataclasses import dataclass
from enum import StrEnum

from baget_storage.core.exceptions import BusinessRuleViolation


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


def validate_document_status(kind: DocumentKind, status: DocumentStatus) -> None:
    allowed = {
        DocumentKind.INVOICE: {DocumentStatus.AVAILABLE},
        DocumentKind.ACT: {DocumentStatus.AVAILABLE, DocumentStatus.SIGNED},
        DocumentKind.CONTRACT: set(DocumentStatus),
    }
    if status not in allowed[kind]:
        raise BusinessRuleViolation("The document status is not valid for this document kind.")


class NotificationCategory(StrEnum):
    SYSTEM = "system"
    PAYMENTS = "payments"
    DOCUMENTS = "documents"
    METERS = "meters"
    TICKETS = "tickets"
    LEASES = "leases"


class NotificationResource(StrEnum):
    INVOICE = "invoice"
    DOCUMENT = "document"
    TICKET = "ticket"
    LEASE = "lease"
    METER = "meter"
    READING = "reading"


@dataclass(frozen=True, slots=True)
class NotificationPreferences:
    notify_payments: bool = True
    notify_meters: bool = True
    notify_documents: bool = True
    notify_tickets: bool = True
    notify_leases: bool = True

    def allows(self, category: NotificationCategory) -> bool:
        return {
            NotificationCategory.SYSTEM: True,
            NotificationCategory.PAYMENTS: self.notify_payments,
            NotificationCategory.METERS: self.notify_meters,
            NotificationCategory.DOCUMENTS: self.notify_documents,
            NotificationCategory.TICKETS: self.notify_tickets,
            NotificationCategory.LEASES: self.notify_leases,
        }[category]
