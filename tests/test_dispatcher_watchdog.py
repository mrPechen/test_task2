"""Тесты диспетчера и сторожа."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from src.database.models import Attempt, LedgerEntry, Task
from src.services.dispatch import dispatch_once
from src.services.orchestration import generate
from src.services.provider_client import ProviderResult
from src.services.watchdog import watchdog_once
from tests.conftest import make_user


class _FailingProviderClient:
    """Заглушка провайдера, который отвечает синхронной ошибкой."""

    async def post_task(
        self, provider, attempt_id, task_id, model_slug, prompt, ref_images
    ):
        return ProviderResult(ok=False, status_code=503)


async def _make_pending_task(session, model_slug="image-fast", ref_images=None):
    """Создать задачу и вернуть task_id (UUID, фиксируем до commit)."""
    user_id = await make_user(session, balance=100)
    task = await generate(session, user_id, model_slug, "hi", ref_images or [])
    await session.flush()
    task_id = task.id
    await session.commit()
    return task_id


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
async def test_dispatch_sync_error_goes_to_next_leg(session):
    task_id = await _make_pending_task(session)

    await dispatch_once(session, _FailingProviderClient())

    attempts = await _attempts(session, task_id)
    assert len(attempts) == 2
    assert attempts[0].status == "failed"
    assert attempts[0].error_type == "sync_error"
    assert attempts[1].provider == "beta"
    assert attempts[1].status == "pending"


async def _mark_sent_in_past(session, attempt_id):
    """Пометить попытку sent с временем в прошлом, чтобы сторож её снял."""
    past = datetime.now(timezone.utc) - timedelta(seconds=100)
    await session.execute(
        update(Attempt)
        .where(Attempt.id == attempt_id)
        .values(status="sent", sent_at=past)
    )
    await session.commit()


@pytest.mark.asyncio
async def test_watchdog_timeout_advances_to_next_leg(session):
    task_id = await _make_pending_task(session)  # image-fast: alpha → beta
    attempt = (
        await session.execute(select(Attempt).where(Attempt.task_id == task_id))
    ).scalar_one()
    await _mark_sent_in_past(session, attempt.id)

    await watchdog_once(session)

    attempts = await _attempts(session, task_id)
    assert attempts[0].status == "timeout"
    assert len(attempts) == 2
    assert attempts[1].provider == "beta"


@pytest.mark.asyncio
async def test_watchdog_timeout_last_leg_refunds_once(session):
    # 5 референсов: image-fast принимает только beta (единственная и последняя нога).
    task_id = await _make_pending_task(session, ref_images=["r"] * 5)
    attempt = (
        await session.execute(select(Attempt).where(Attempt.task_id == task_id))
    ).scalar_one()
    assert attempt.provider == "beta"
    await _mark_sent_in_past(session, attempt.id)

    await watchdog_once(session)

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
