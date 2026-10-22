"""Pydantic-схемы сервиса генерации.

Внешний API использует snake_case (по ТЗ), а не camelCase: контракт тестового
задания важнее кодстайла. Внутренний код следует кодстайлу Dima.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GenerateRequest(BaseModel):
    """Тело запроса POST /generate."""

    user_id: UUID
    model_slug: str
    prompt: str
    ref_images: list[str] = Field(default_factory=list)


class GenerateResponse(BaseModel):
    """Ответ POST /generate."""

    task_id: UUID


class AttemptHistory(BaseModel):
    """Строка истории попыток в ответе GET /tasks/{id}."""

    model_config = ConfigDict(from_attributes=True)

    provider: str
    status: str
    error_type: str | None
    sent_at: datetime | None
    created_at: datetime


class TaskResponse(BaseModel):
    """Ответ GET /tasks/{id}."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    model_slug: str
    price_tokens: int
    current_leg: str | None
    result_url: str | None
    error_type: str | None
    attempts: list[AttemptHistory]


class LedgerEntryResponse(BaseModel):
    """Строка журнала в ответе GET /users/{id}/balance."""

    model_config = ConfigDict(from_attributes=True)

    kind: str
    amount: int
    task_id: UUID | None
    payment_id: str | None
    created_at: datetime


class BalanceResponse(BaseModel):
    """Ответ GET /users/{id}/balance."""

    user_id: UUID
    balance: int
    ledger: list[LedgerEntryResponse]


class ProviderWebhook(BaseModel):
    """Тело вебхука от провайдера."""

    event_id: str
    attempt_id: str
    task_id: UUID | None = None
    external_id: str | None = None
    result: str  # completed | failed
    error_type: str | None = None
    file_url: str | None = None


class PaymentWebhook(BaseModel):
    """Тело вебхука платежей."""

    payment_id: str
    user_id: UUID
    tokens: int = Field(ge=1)
    status: str
