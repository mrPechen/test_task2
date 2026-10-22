"""Тесты маршрутизации ног, возврата и начисления (оркестрация напрямую)."""

import pytest
from sqlalchemy import select

from src.database.models import Attempt, LedgerEntry, Payment, Task
from src.schemas import PaymentWebhook
from src.services.orchestration import (
    advance,
    fail_task,
    generate,
    handle_payment_webhook,
)
from src.services.queries.task_queries import TaskQuery
from tests.conftest import make_user


async def _generate(session, user_id, model_slug="image-fast", ref_images=None):
    """Создать задачу и вернуть task_id (UUID, фиксируем до commit)."""
    task = await generate(session, user_id, model_slug, "hi", ref_images or [])
    return task.id


async def _attempts(session, task_id):
    return (
        (
            await session.execute(
                select(Attempt)
                .where(Attempt.task_id == task_id)
                .order_by(Attempt.attempt_no)
            )
        )
        .scalars()
        .all()
    )


@pytest.mark.asyncio
async def test_technical_failure_goes_to_next_leg(session):
    user_id = await make_user(session, balance=10)
    task_id = await _generate(session, user_id)
    attempt1 = (
        await session.execute(select(Attempt).where(Attempt.task_id == task_id))
    ).scalar_one()

    # Блокируем задачу и прогоняем отказ первой ноги (alpha).
    locked_task = await TaskQuery(session).lock_task(task_id)
    await advance(session, locked_task, attempt1, "technical")
    await session.commit()

    attempts = await _attempts(session, task_id)
    assert len(attempts) == 2
    assert attempts[1].provider == "beta"
    assert attempts[1].status == "pending"


@pytest.mark.asyncio
async def test_moderation_skips_skip_on_moderation_and_refunds(session):
    user_id = await make_user(session, balance=100)
    # video-long: beta(1) → alpha(2, skip_on_moderation).
    task_id = await _generate(
        session, user_id, model_slug="video-long", ref_images=["r"]
    )
    attempt1 = (
        await session.execute(select(Attempt).where(Attempt.task_id == task_id))
    ).scalar_one()
    assert attempt1.provider == "beta"

    locked_task = await TaskQuery(session).lock_task(task_id)
    await advance(session, locked_task, attempt1, "moderation")
    await session.commit()

    # alpha имеет skip_on_moderation → пропускается → failed + возврат.
    refreshed = (
        await session.execute(select(Task).where(Task.id == task_id))
    ).scalar_one()
    assert refreshed.status == "failed"
    refunds = (
        (
            await session.execute(
                select(LedgerEntry).where(
                    LedgerEntry.task_id == task_id, LedgerEntry.kind == "refund"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(refunds) == 1
    assert refunds[0].amount == 25


@pytest.mark.asyncio
async def test_payload_fails_immediately_without_next_leg(session):
    user_id = await make_user(session, balance=10)
    task_id = await _generate(session, user_id)
    attempt1 = (
        await session.execute(select(Attempt).where(Attempt.task_id == task_id))
    ).scalar_one()

    locked_task = await TaskQuery(session).lock_task(task_id)
    await advance(session, locked_task, attempt1, "payload")
    await session.commit()

    refreshed = (
        await session.execute(select(Task).where(Task.id == task_id))
    ).scalar_one()
    assert refreshed.status == "failed"
    attempts = await _attempts(session, task_id)
    assert len(attempts) == 1  # вторая нога не пробовалась


@pytest.mark.asyncio
async def test_refund_idempotent_on_repeated_fail(session):
    user_id = await make_user(session, balance=10)
    task_id = await _generate(session, user_id)
    locked = await TaskQuery(session).lock_task(task_id)
    await fail_task(session, locked, "technical")
    await session.commit()

    # Повторный fail той же задачи не создаёт второй возврат.
    locked2 = await TaskQuery(session).lock_task(task_id)
    await fail_task(session, locked2, "technical")
    await session.commit()

    refunds = (
        (
            await session.execute(
                select(LedgerEntry).where(
                    LedgerEntry.task_id == task_id, LedgerEntry.kind == "refund"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(refunds) == 1


@pytest.mark.asyncio
async def test_topup_idempotent(session):
    user_id = await make_user(session, balance=0)
    payload = PaymentWebhook(
        payment_id="pay-1", user_id=user_id, tokens=50, status="paid"
    )

    first = await handle_payment_webhook(session, payload)
    second = await handle_payment_webhook(session, payload)
    assert first == "credited"
    assert second == "duplicate"

    topups = (
        (
            await session.execute(
                select(LedgerEntry).where(LedgerEntry.payment_id == "pay-1")
            )
        )
        .scalars()
        .all()
    )
    assert len(topups) == 1
    assert topups[0].amount == 50

    payments = (
        (await session.execute(select(Payment).where(Payment.payment_id == "pay-1")))
        .scalars()
        .all()
    )
    assert len(payments) == 1


@pytest.mark.asyncio
async def test_topup_non_paid_status_ignored(session):
    user_id = await make_user(session, balance=0)
    payload = PaymentWebhook(
        payment_id="pay-2", user_id=user_id, tokens=50, status="pending"
    )
    result = await handle_payment_webhook(session, payload)
    assert result == "ignored"
    topups = (
        (await session.execute(select(LedgerEntry).where(LedgerEntry.kind == "topup")))
        .scalars()
        .all()
    )
    assert topups == []


@pytest.mark.asyncio
async def test_topup_paid_after_intermediate_status(session):
    """Промежуточный статус не должен «съедать» payment_id (двухфазный платёж)."""
    user_id = await make_user(session, balance=0)
    pending = PaymentWebhook(
        payment_id="pay-3", user_id=user_id, tokens=50, status="pending"
    )
    assert await handle_payment_webhook(session, pending) == "ignored"

    paid = PaymentWebhook(payment_id="pay-3", user_id=user_id, tokens=50, status="paid")
    assert await handle_payment_webhook(session, paid) == "credited"

    topups = (
        (
            await session.execute(
                select(LedgerEntry).where(LedgerEntry.payment_id == "pay-3")
            )
        )
        .scalars()
        .all()
    )
    assert len(topups) == 1
    assert topups[0].amount == 50
