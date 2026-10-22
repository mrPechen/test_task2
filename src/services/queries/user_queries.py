"""Чистые запросы к БД: пользователи и журнал операций."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import LedgerEntry, User
from src.utils.exceptions import NotFoundError


class UserQuery:
    """Запросы к пользователям и журналу."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_user(self, user_id: UUID) -> User:
        """Получить пользователя.

        :param user_id: идентификатор пользователя
        :return: пользователь
        :raises NotFoundError: если пользователя нет
        """
        user = (
            await self.session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if user is None:
            raise NotFoundError(f"Пользователь {user_id} не найден")
        return user

    async def lock_user(self, user_id: UUID) -> User | None:
        """Заблокировать строку пользователя (SELECT FOR UPDATE).

        :param user_id: идентификатор пользователя
        :return: пользователь или None
        """
        return (
            await self.session.execute(
                select(User).where(User.id == user_id).with_for_update()
            )
        ).scalar_one_or_none()

    async def get_recent_ledger(
        self, user_id: UUID, limit: int = 20
    ) -> list[LedgerEntry]:
        """Получить последние строки журнала пользователя.

        :param user_id: идентификатор пользователя
        :param limit: число строк
        :return: список строк журнала (свежие первыми)
        """
        result = await self.session.execute(
            select(LedgerEntry)
            .where(LedgerEntry.user_id == user_id)
            .order_by(LedgerEntry.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
