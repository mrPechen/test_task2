"""Обязательные интеграционные тесты (живой compose, 4 воркера).

Проверяют «ровно один раз» для начисления и возврата, маршрутизацию по ногам
и правило 6 (поздний completed после сторожа).
"""

import asyncio
import uuid

import pytest

from tests.integration.conftest import (
    ALPHA_ADMIN,
    BETA_ADMIN,
    create_user,
    db_scalar,
    post_signed_webhook,
    reset_provider,
    set_provider,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_20_concurrent_payments_credit_once(client, db):
    user_id = await create_user(db, balance=0)
    payment_id = f"pay-{uuid.uuid4()}"
    body = {
        "payment_id": payment_id,
        "user_id": str(user_id),
        "tokens": 50,
        "status": "paid",
    }

    async def send():
        return await post_signed_webhook(client, "/webhooks/payments", body)

    results = await asyncio.gather(*[send() for _ in range(20)])
    assert all(r.status_code == 200 for r in results)

    topups = await db_scalar(
        db,
        "SELECT count(*) FROM ledger_entries WHERE payment_id = :p AND kind = 'topup'",
        p=payment_id,
    )
    assert topups == 1

    balance = await db_scalar(db, "SELECT balance FROM users WHERE id = :u", u=user_id)
    assert balance == 50


@pytest.mark.asyncio
async def test_10_concurrent_failed_on_last_leg_refund_once(client, db):
    await reset_provider(BETA_ADMIN)
    # beta молчит (drop_webhook), чтобы не мешал нашим вебхукам.
    await set_provider(BETA_ADMIN, drop_webhook=True, delay_min=0.05, delay_max=0.1)

    user_id = await create_user(db, balance=100)
    # 5 референсов: image-fast принимает только beta — единственная (последняя) нога.
    resp = await client.post(
        "/generate",
        json={
            "user_id": str(user_id),
            "model_slug": "image-fast",
            "prompt": "hi",
            "ref_images": ["r"] * 5,
        },
    )
    assert resp.status_code == 200
    task_id = uuid.UUID(resp.json()["task_id"])

    # Дождаться, пока диспетчер отправит попытку в beta.
    attempt_id = None
    for _ in range(50):
        attempt_id = await db_scalar(
            db,
            "SELECT attempt_id FROM attempts WHERE task_id = :t AND status = 'sent'",
            t=task_id,
        )
        if attempt_id:
            break
        await asyncio.sleep(0.1)
    assert attempt_id is not None

    # 10 одновременных failed-вебхуков по последней ноге.
    async def send(i: int):
        body = {
            "event_id": f"evt-{task_id}-{i}",
            "attempt_id": attempt_id,
            "task_id": str(task_id),
            "result": "failed",
            "error_type": "technical",
        }
        return await post_signed_webhook(client, "/webhooks/providers/beta", body)

    results = await asyncio.gather(*[send(i) for i in range(10)])
    assert all(r.status_code == 200 for r in results)

    refunds = await db_scalar(
        db,
        "SELECT count(*) FROM ledger_entries WHERE task_id = :t AND kind = 'refund'",
        t=task_id,
    )
    assert refunds == 1
    # Баланс вернулся ровно один раз (100 - 2 + 2 = 100).
    balance = await db_scalar(db, "SELECT balance FROM users WHERE id = :u", u=user_id)
    assert balance == 100


@pytest.mark.asyncio
async def test_first_leg_fails_second_leg_succeeds(client, db):
    await reset_provider(ALPHA_ADMIN)
    await reset_provider(BETA_ADMIN)
    # alpha отказывает technical, beta — успех.
    await set_provider(
        ALPHA_ADMIN,
        failure_rate=1.0,
        error_type="technical",
        delay_min=0.05,
        delay_max=0.1,
    )
    await set_provider(BETA_ADMIN, failure_rate=0.0, delay_min=0.05, delay_max=0.1)

    user_id = await create_user(db, balance=100)
    resp = await client.post(
        "/generate",
        json={
            "user_id": str(user_id),
            "model_slug": "image-fast",
            "prompt": "hi",
            "ref_images": [],
        },
    )
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]

    # Ждём завершения на второй ноге.
    status = None
    for _ in range(100):
        r = await client.get(f"/tasks/{task_id}")
        status = r.json()["status"]
        if status in ("succeeded", "failed"):
            break
        await asyncio.sleep(0.1)
    assert status == "succeeded"

    data = (await client.get(f"/tasks/{task_id}")).json()
    assert len(data["attempts"]) == 2
    assert data["attempts"][0]["provider"] == "alpha"
    assert data["attempts"][0]["error_type"] == "technical"
    assert data["attempts"][1]["provider"] == "beta"
    assert data["attempts"][1]["status"] == "succeeded"

    # Возврата быть не должно.
    refunds = await db_scalar(
        db,
        "SELECT count(*) FROM ledger_entries WHERE task_id = :t AND kind = 'refund'",
        t=task_id,
    )
    assert refunds == 0


@pytest.mark.asyncio
async def test_late_completed_after_watchdog_rule6(client, db):
    await reset_provider(BETA_ADMIN)
    # beta пришлёт completed ПОЗЖЕ, чем сработает сторож (PROVIDER_TIMEOUT=2).
    await set_provider(
        BETA_ADMIN, late_completed=True, late_delay=3.0, delay_min=0.05, delay_max=0.1
    )

    user_id = await create_user(db, balance=100)
    resp = await client.post(
        "/generate",
        json={
            "user_id": str(user_id),
            "model_slug": "image-fast",
            "prompt": "hi",
            "ref_images": ["r"] * 5,
        },
    )
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]

    # Ждём, когда сторож снимет попытку и вернёт токены.
    status = None
    for _ in range(100):
        r = await client.get(f"/tasks/{task_id}")
        status = r.json()["status"]
        if status == "failed":
            break
        await asyncio.sleep(0.1)
    assert status == "failed"

    # Один возврат.
    refunds = await db_scalar(
        db,
        "SELECT count(*) FROM ledger_entries WHERE task_id = :t AND kind = 'refund'",
        t=task_id,
    )
    assert refunds == 1

    # Ждём поздний completed и проверяем правило 6: результат не выдан, денег не двигали.
    await asyncio.sleep(2.0)
    late = await db_scalar(
        db,
        "SELECT count(*) FROM attempts WHERE task_id = :t AND status = 'late_completed'",
        t=task_id,
    )
    assert late == 1

    data = (await client.get(f"/tasks/{task_id}")).json()
    assert data["status"] == "failed"
    assert data["result_url"] is None
    refunds_after = await db_scalar(
        db,
        "SELECT count(*) FROM ledger_entries WHERE task_id = :t AND kind = 'refund'",
        t=task_id,
    )
    assert refunds_after == 1
