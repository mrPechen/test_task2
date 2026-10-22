"""Общие фикстуры для юнит-тестов.

Тесты работают против настоящего PostgreSQL (порт из docker compose), но
схему создают через create_all и чистят таблицы между тестами — это быстро
и детерминированно. Интеграционные тесты (tests/integration) отдельно ходят
в живой compose-стек через HTTP.
"""

import os
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

# URL тестовой базы задаётся ДО импорта приложения: настройки frozen читаются
# из env при импорте config.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://generation:generation@localhost:5432/generation_test",
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from main import app  # noqa: E402
from src.database.db_settings import Base  # noqa: E402
from src.database.models import LedgerEntry, Leg, Model, User  # noqa: E402


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture(scope="session")
async def _engine():
    """Создать схему в тестовой базе один раз на сессию."""
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


async def _seed_catalog(session: AsyncSession) -> None:
    """Наполнить каталог моделями и ногами (как в data-миграции)."""
    session.add_all(
        [
            Model(slug="image-fast", name="Image Fast", price_tokens=2),
            Model(slug="video-long", name="Video Long", price_tokens=25),
        ]
    )
    await session.flush()
    session.add_all(
        [
            Leg(
                id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
                model_slug="image-fast",
                provider="alpha",
                priority=1,
                max_ref_images=4,
                skip_on_moderation=False,
            ),
            Leg(
                id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
                model_slug="image-fast",
                provider="beta",
                priority=2,
                max_ref_images=10,
                skip_on_moderation=False,
            ),
            Leg(
                id=uuid.UUID("00000000-0000-0000-0000-000000000003"),
                model_slug="video-long",
                provider="beta",
                priority=1,
                max_ref_images=2,
                skip_on_moderation=False,
            ),
            Leg(
                id=uuid.UUID("00000000-0000-0000-0000-000000000004"),
                model_slug="video-long",
                provider="alpha",
                priority=2,
                max_ref_images=1,
                skip_on_moderation=True,
            ),
        ]
    )
    await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def _clean_db(_engine):
    """Очистить таблицы и засеять каталог перед каждым тестом."""
    async with _engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE TABLE webhook_events, payments, ledger_entries, "
                "attempts, tasks, legs, models_catalog, users "
                "RESTART IDENTITY CASCADE"
            )
        )
    async with AsyncSession(_engine, expire_on_commit=False) as session:
        await _seed_catalog(session)
    yield


@pytest_asyncio.fixture
async def session(_engine):
    """Сессия для теста (expire_on_commit=True — объекты обновляются после commit)."""
    async with AsyncSession(_engine) as s:
        yield s


@pytest_asyncio.fixture
async def client():
    """HTTP-клиент через ASGITransport (без lifespan — фоновые циклы не стартуют)."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def make_user(session: AsyncSession, balance: int = 100) -> uuid.UUID:
    """Создать пользователя с балансом, сохраняя инвариант журнала.

    Возвращает UUID пользователя (не ORM-объект — после commit объект expired,
    и доступ к атрибутам вызвал бы lazy-load).

    :param session: сессия БД
    :param balance: начальный баланс
    :return: id пользователя
    """
    user = User(balance=0)
    session.add(user)
    await session.flush()
    user_id = user.id
    if balance > 0:
        session.add(
            LedgerEntry(
                user_id=user_id,
                kind="topup",
                amount=balance,
                payment_id=f"seed-{uuid.uuid4()}",
            )
        )
        await session.execute(
            update(User).where(User.id == user_id).values(balance=balance)
        )
    await session.commit()
    return user_id


__all__ = ["app", "make_user", "TEST_DATABASE_URL"]
