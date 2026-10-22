"""Тесты вебхуков провайдера (HMAC, дедуп, терминальность, правило 6)."""

import json

import pytest
from sqlalchemy import select, update

from config import settings
from src.database.models import Attempt, LedgerEntry, Task
from src.services.signature import sign_body
from tests.conftest import make_user

SECRET = settings.WEBHOOK_SECRET


def _sign(raw: bytes) -> str:
    return sign_body(raw, SECRET)


def _body(event_id: str, attempt_id: str, result: str, error_type: str | None = None):
    return {
        "event_id": event_id,
        "attempt_id": attempt_id,
        "result": result,
        "error_type": error_type,
        "file_url": "http://files/x.png" if result == "completed" else None,
    }


async def _post(client, provider: str, body: dict, signature: str | None = None):
    raw = json.dumps(body, separators=(",", ":")).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Signature": signature or _sign(raw),
    }
    return await client.post(
        f"/webhooks/providers/{provider}", content=raw, headers=headers
    )


async def _make_sent_attempt(session, model_slug="image-fast", ref_images=None):
    """Создать задачу и вернуть (task_id, attempt_id), пометив попытку sent."""
    from src.services.orchestration import generate

    user_id = await make_user(session, balance=100)
    task = await generate(session, user_id, model_slug, "hi", ref_images or [])
    await session.flush()
    task_id = task.id  # фиксируем ДО commit
    attempt = (
        await session.execute(select(Attempt).where(Attempt.task_id == task_id))
    ).scalar_one()
    attempt_id = attempt.attempt_id  # фиксируем ДО commit (иначе lazy-load)
    await session.execute(
        update(Attempt).where(Attempt.id == attempt.id).values(status="sent")
    )
    await session.commit()
    return task_id, attempt_id


async def _get_attempt_by_public_id(session, attempt_id: str) -> Attempt:
    return (
        await session.execute(select(Attempt).where(Attempt.attempt_id == attempt_id))
    ).scalar_one()


@pytest.mark.asyncio
async def test_webhook_invalid_signature_401(client, session):
    task_id, attempt_id = await _make_sent_attempt(session)
    body = _body("evt-1", attempt_id, "completed")
    resp = await _post(client, "alpha", body, signature="deadbeef")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_webhook_completed_succeeds(client, session):
    task_id, attempt_id = await _make_sent_attempt(session)
    body = _body("evt-2", attempt_id, "completed")
    resp = await _post(client, "alpha", body)
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"

    refreshed = (
        await session.execute(select(Task).where(Task.id == task_id))
    ).scalar_one()
    assert refreshed.status == "succeeded"
    assert refreshed.result_url == "http://files/x.png"
    # Деньги не двигались (только списание при создании).
    refunds = (
        (await session.execute(select(LedgerEntry).where(LedgerEntry.kind == "refund")))
        .scalars()
        .all()
    )
    assert refunds == []


@pytest.mark.asyncio
async def test_webhook_duplicate_ignored(client, session):
    task_id, attempt_id = await _make_sent_attempt(session)
    body = _body("evt-3", attempt_id, "completed")
    first = await _post(client, "alpha", body)
    second = await _post(client, "alpha", body)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"

    refreshed = (
        await session.execute(select(Task).where(Task.id == task_id))
    ).scalar_one()
    assert refreshed.status == "succeeded"


@pytest.mark.asyncio
async def test_webhook_failed_technical_advances(client, session):
    task_id, attempt_id = await _make_sent_attempt(session)
    body = _body("evt-4", attempt_id, "failed", error_type="technical")
    resp = await _post(client, "alpha", body)
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"

    attempts = (
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
    assert len(attempts) == 2
    assert attempts[0].status == "failed"
    assert attempts[1].provider == "beta"


@pytest.mark.asyncio
async def test_late_completed_after_timeout_no_money(client, session):
    task_id, attempt_id = await _make_sent_attempt(session)
    # Сторож уже снял попытку.
    attempt = await _get_attempt_by_public_id(session, attempt_id)
    await session.execute(
        update(Attempt).where(Attempt.id == attempt.id).values(status="timeout")
    )
    await session.commit()

    body = _body("evt-5", attempt_id, "completed")
    resp = await _post(client, "alpha", body)
    assert resp.status_code == 200
    assert resp.json()["status"] == "late"

    refreshed = (
        await session.execute(select(Task).where(Task.id == task_id))
    ).scalar_one()
    assert refreshed.status != "succeeded"  # результат НЕ выдан
    refunds = (
        (await session.execute(select(LedgerEntry).where(LedgerEntry.kind == "refund")))
        .scalars()
        .all()
    )
    assert refunds == []  # возврата нет (задача ещё не failed)

    refreshed_attempt = await _get_attempt_by_public_id(session, attempt_id)
    assert refreshed_attempt.status == "late_completed"
