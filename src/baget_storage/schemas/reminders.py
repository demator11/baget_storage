from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class InvoiceReminder(BaseModel):
    id: UUID
    user_id: UUID
    number: str
    due_date: date
    remaining_amount: Decimal


class LeaseReminder(BaseModel):
    id: UUID
    user_id: UUID
    number: str
    end_date: date


class ReadingReminder(BaseModel):
    meter_id: UUID
    user_id: UUID
    serial_number: str


class ReminderRunResult(BaseModel):
    invoices_checked: int = 0
    leases_checked: int = 0
    readings_checked: int = 0
