"""Роуты чтения задач."""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.db import get_async_session
from src.handlers.task_handler import TaskHandler
from src.schemas import TaskResponse

router = APIRouter()

_handler = TaskHandler()


@router.get("/tasks/{task_id}", summary="Статус задачи", response_model=TaskResponse)
async def get_task(
    task_id: UUID,
    session: AsyncSession = Depends(get_async_session),
) -> TaskResponse:
    """Вернуть статус, текущую ногу и историю попыток."""
    return await _handler.get(session, task_id)
