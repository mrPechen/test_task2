"""Роуты чтения баланса пользователя."""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.db import get_async_session
from src.handlers.user_handler import UserHandler
from src.schemas import BalanceResponse

router = APIRouter()

_handler = UserHandler()


@router.get(
    "/users/{user_id}/balance",
    summary="Баланс пользователя",
    response_model=BalanceResponse,
)
async def get_balance(
    user_id: UUID,
    session: AsyncSession = Depends(get_async_session),
) -> BalanceResponse:
    """Вернуть баланс и последние строки журнала."""
    return await _handler.get_balance(session, user_id)
