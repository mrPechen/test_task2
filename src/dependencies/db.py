"""Генераторы зависимостей FastAPI (сессии БД)."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from src.database.db_settings import SessionLocal


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Создать сессию на время обработки запроса.

    :return: AsyncSession, закрывается после завершения запроса
    """
    async with SessionLocal() as session:
        yield session
