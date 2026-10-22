"""Инициализация асинхронного движка SQLAlchemy и базового класса моделей."""

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

from config import settings


class Base(DeclarativeBase):
    """Базовый класс для всех ORM-моделей."""


# NullPool по кодстайлу: новое подключение на каждый запрос.
# Для тестовой задачи это допустимо; на проде заменяется на пул/PgBouncer.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    poolclass=NullPool,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    class_=AsyncSession,
    bind=engine,
)
