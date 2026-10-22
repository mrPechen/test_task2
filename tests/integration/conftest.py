"""Фикстуры интеграционных тестов (живой compose-стек).

Тесты ходят по настоящему HTTP к сервису (4 воркера) и проверяют БД напрямую.
Стек поднимается так:

    docker compose -f docker-compose.yml -f docker-compose.test.yml up -d --build
"""

import hashlib
import hmac
import json
import os
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost:8000")
DB_URL = os.environ.get(
    "INTEGRATION_DATABASE_URL",
    "postgresql+asyncpg://generation:generation@localhost:5432/generation",
)
SECRET = os.environ.get("WEBHOOK_SECRET", "test-secret")
ALPHA_ADMIN = os.environ.get("ALPHA_ADMIN_URL", "http://localhost:8001")
BETA_ADMIN = os.environ.get("BETA_ADMIN_URL", "http://localhost:8002")


def sign(raw: bytes) -> str:
    """HMAC-SHA256 hex подпись (как в сервисе и фейковых провайдерах)."""
    return hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture
async def _check_stack():
    """Проверить доступность сервиса; пропустить тесты, если стек не поднят."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=2.0) as c:
        try:
            await c.get("/users/00000000-0000-0000-0000-000000000000/balance")
        except httpx.HTTPError:
            pytest.skip("Стек не поднят: сначала `docker compose ... up -d --build`")


@pytest_asyncio.fixture
async def client(_check_stack):
    """HTTP-клиент к живому сервису."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as c:
        yield c


@pytest_asyncio.fixture
async def db():
    """Движок к базе для проверки журнала/баланса."""
    engine = create_async_engine(DB_URL, poolclass=NullPool)
    yield engine
    await engine.dispose()


async def create_user(db, balance: int) -> uuid.UUID:
    """Создать пользователя с балансом (журнал + баланс консистентны)."""
    user_id = uuid.uuid4()
    async with db.begin() as conn:
        await conn.execute(
            text("INSERT INTO users (id, balance) VALUES (:id, 0)"), {"id": user_id}
        )
        if balance > 0:
            await conn.execute(
                text(
                    "INSERT INTO ledger_entries (user_id, kind, amount, payment_id) "
                    "VALUES (:uid, 'topup', :amt, :pid)"
                ),
                {"uid": user_id, "amt": balance, "pid": f"seed-{uuid.uuid4()}"},
            )
            await conn.execute(
                text("UPDATE users SET balance = :amt WHERE id = :id"),
                {"amt": balance, "id": user_id},
            )
    return user_id


async def set_provider(admin_url: str, **behavior) -> None:
    """Настроить поведение фейкового провайдера через PUT /_settings."""
    async with httpx.AsyncClient(base_url=admin_url, timeout=10.0) as c:
        resp = await c.put("/_settings", json=behavior)
        assert resp.status_code == 200, resp.text


async def reset_provider(admin_url: str) -> None:
    """Сбросить состояние провайдера."""
    async with httpx.AsyncClient(base_url=admin_url, timeout=10.0) as c:
        await c.post("/_reset")


async def post_signed_webhook(client, path: str, body: dict) -> httpx.Response:
    """Отправить подписанный вебхук на сервис."""
    raw = json.dumps(body, separators=(",", ":")).encode()
    return await client.post(
        path,
        content=raw,
        headers={"Content-Type": "application/json", "X-Signature": sign(raw)},
    )


async def db_scalar(db, query: str, **params):
    """Выполнить скалярный SQL-запрос и вернуть единственное значение."""
    async with db.connect() as conn:
        return (await conn.execute(text(query), params)).scalar()
