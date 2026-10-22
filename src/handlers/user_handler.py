"""Handler чтения баланса пользователя."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.schemas import BalanceResponse, LedgerEntryResponse
from src.services.queries.user_queries import UserQuery
from src.utils.exceptions import NotFoundError


class UserHandler:
    """Чтение баланса и журнала операций."""

    async def get_balance(
        self, session: AsyncSession, user_id: UUID
    ) -> BalanceResponse:
        """Получить баланс и последние строки журнала.

        :param session: сессия БД
        :param user_id: идентификатор пользователя
        :return: ответ с балансом и журналом
        """
        query = UserQuery(session)
        try:
            user = await query.get_user(user_id)
        except NotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        ledger = [
            LedgerEntryResponse(
                kind=e.kind,
                amount=e.amount,
                task_id=e.task_id,
                payment_id=e.payment_id,
                created_at=e.created_at,
            )
            for e in await query.get_recent_ledger(user_id)
        ]
        return BalanceResponse(user_id=user.id, balance=user.balance, ledger=ledger)
