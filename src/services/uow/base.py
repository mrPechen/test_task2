"""Абстрактный Unit of Work — границы транзакций."""

from abc import ABC

from sqlalchemy.ext.asyncio import AsyncSession


class AbstractUnitOfWork(ABC):
    """Базовый UoW: старт транзакции, commit/rollback по результату."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def __aenter__(self) -> "AbstractUnitOfWork":
        await self.session.begin()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            await self.rollback()
        else:
            await self.commit()

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()
