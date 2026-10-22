"""Тесты POST /generate."""

import uuid

import pytest
from sqlalchemy import func, select

from src.database.models import Attempt, LedgerEntry, Task
from tests.conftest import make_user


async def _generate(
    client, user_id, model_slug="image-fast", ref_images=None, prompt="hi"
):
    return await client.post(
        "/generate",
        json={
            "user_id": str(user_id),
            "model_slug": model_slug,
            "prompt": prompt,
            "ref_images": ref_images or [],
        },
    )


@pytest.mark.asyncio
async def test_generate_success_debits_and_creates_task(client, session):
    user_id = await make_user(session, balance=10)
    resp = await _generate(client, user_id)
    assert resp.status_code == 200
    task_id = uuid.UUID(resp.json()["task_id"])

    # Списание ровно одно и в журнале.
    debit = (
        (
            await session.execute(
                select(LedgerEntry).where(LedgerEntry.task_id == task_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(debit) == 1
    assert debit[0].kind == "debit"
    assert debit[0].amount == -2

    # Задача создана со снапшотом цены и первой попыткой.
    task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
    assert task.price_tokens == 2
    assert task.status == "running"
    attempt = (
        await session.execute(select(Attempt).where(Attempt.task_id == task_id))
    ).scalar_one()
    assert attempt.provider == "alpha"

    # Баланс уменьшен (сумма журнала).
    balance = (
        await session.execute(
            select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
                LedgerEntry.user_id == user_id
            )
        )
    ).scalar_one()
    assert balance == 8


@pytest.mark.asyncio
async def test_generate_insufficient_tokens_402(client, session):
    user_id = await make_user(session, balance=1)
    resp = await _generate(client, user_id)  # image-fast стоит 2
    assert resp.status_code == 402
    # Задача не создана, списания нет.
    tasks = (await session.execute(select(Task))).scalars().all()
    assert tasks == []


@pytest.mark.asyncio
async def test_generate_limit_429_before_402(client, session):
    user_id = await make_user(session, balance=100)
    # Три активные задачи по image-fast (цена 2).
    for _ in range(3):
        resp = await _generate(client, user_id)
        assert resp.status_code == 200

    # Четвёртая — видео (цена 25 > баланса 94), но лимит активных раньше.
    resp = await _generate(client, user_id, model_slug="video-long")
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_generate_no_fitting_leg_400(client, session):
    user_id = await make_user(session, balance=100)
    # image-fast принимает максимум 10 референсов.
    resp = await _generate(client, user_id, ref_images=["r"] * 11)
    assert resp.status_code == 400
    tasks = (await session.execute(select(Task))).scalars().all()
    assert tasks == []


@pytest.mark.asyncio
async def test_generate_unknown_model_404(client, session):
    user_id = await make_user(session, balance=100)
    resp = await _generate(client, user_id, model_slug="nope")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_generate_unknown_user_404(client, session):
    resp = await _generate(client, uuid.uuid4())
    assert resp.status_code == 404
