"""ORM-модели предметной области сервиса генерации.

Все модели объявлены в одном файле по кодстайлу. Идентификаторы — UUID,
деньги — целые токены (BIGINT), время — timestamptz (UTC, берётся из Postgres).
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.db_settings import Base


class TaskStatus(StrEnum):
    """Статусы задачи генерации."""

    PENDING = "pending"  # создана, первая попытка ещё не отправлена
    RUNNING = "running"  # попытка отправлена провайдеру, ждём вебхук
    SUCCEEDED = "succeeded"  # терминальный успех
    FAILED = "failed"  # терминальная неудача (токены возвращены)


class AttemptStatus(StrEnum):
    """Статусы отдельной попытки выполнить задачу через провайдера."""

    PENDING = "pending"  # создана, ждёт диспетчера
    SENT = "sent"  # отправлена провайдеру, ждём вебхук
    SUCCEEDED = "succeeded"  # провайдер вернул completed
    FAILED = "failed"  # провайдер вернул failed
    TIMEOUT = "timeout"  # сторож снял попытку по таймауту
    LATE_COMPLETED = "late_completed"  # completed пришёл после таймаута (аудит)


class LedgerKind(StrEnum):
    """Типы строк журнала операций."""

    DEBIT = "debit"  # списание за генерацию
    REFUND = "refund"  # возврат при неудаче
    TOPUP = "topup"  # начисление от платежа


class ErrorType(StrEnum):
    """Типы ошибок провайдера."""

    TECHNICAL = "technical"
    MODERATION = "moderation"
    PAYLOAD = "payload"
    SYNC_ERROR = "sync_error"  # 5xx/таймаут на отправку
    TIMEOUT = "timeout"  # сторож снял попытку


class User(Base):
    """Пользователь с балансом токенов.

    Баланс — материализованная копия суммы журнала (invariant). Изменяется
    только в одной транзакции со вставкой строки журнала.
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("balance >= 0", name="ck_users_balance_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    balance: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    ledger_entries: Mapped[list["LedgerEntry"]] = relationship(back_populates="user")


class LedgerEntry(Base):
    """Строка журнала операций (append-only).

    Каждая денежная операция — ровно одна строка. amount подписанный:
    debit — отрицательный, refund/topup — положительный. Баланс = SUM(amount).
    """

    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint("amount != 0", name="ck_ledger_amount_non_zero"),
        # Списание и возврат уникальны по задаче.
        Index(
            "uq_ledger_task_kind",
            "task_id",
            "kind",
            unique=True,
            postgresql_where="kind IN ('debit', 'refund')",
        ),
        # Начисление уникально по платежу.
        Index(
            "uq_ledger_payment_kind",
            "payment_id",
            "kind",
            unique=True,
            postgresql_where="kind = 'topup'",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="ledger_entries")


class Payment(Base):
    """Запись о входящем платеже (идемпотентность по payment_id)."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    payment_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Model(Base):
    """Каталог моделей генерации."""

    __tablename__ = "models_catalog"

    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    price_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    legs: Mapped[list["Leg"]] = relationship(back_populates="model")


class Leg(Base):
    """Нога — способ выполнить модель через конкретного провайдера."""

    __tablename__ = "legs"
    __table_args__ = (
        UniqueConstraint("model_slug", "priority", name="uq_legs_model_priority"),
        CheckConstraint("max_ref_images >= 0", name="ck_legs_max_ref_images"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    model_slug: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("models_catalog.slug", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    max_ref_images: Mapped[int] = mapped_column(Integer, nullable=False)
    skip_on_moderation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    model: Mapped["Model"] = relationship(back_populates="legs")


class Task(Base):
    """Задача генерации."""

    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_user_status", "user_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_slug: Mapped[str] = mapped_column(
        String(64), ForeignKey("models_catalog.slug"), nullable=False
    )
    price_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )  # снапшот цены
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    ref_images: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    ref_images_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TaskStatus.PENDING
    )
    current_leg_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    result_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    attempts: Mapped[list["Attempt"]] = relationship(back_populates="task")


class Attempt(Base):
    """Попытка выполнить задачу через одну ногу."""

    __tablename__ = "attempts"
    __table_args__ = (
        UniqueConstraint("task_id", "leg_id", name="uq_attempts_task_leg"),
        Index("ix_attempts_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    leg_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("legs.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attempt_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=AttemptStatus.PENDING
    )
    error_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    task: Mapped["Task"] = relationship(back_populates="attempts")


class WebhookEvent(Base):
    """Событие вебхука провайдера (дедуп по provider + event_id)."""

    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_webhook_provider_event"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
