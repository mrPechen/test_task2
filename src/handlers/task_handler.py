"""Handler чтения задачи."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.schemas import AttemptHistory, TaskResponse
from src.services.queries.task_queries import TaskQuery
from src.utils.exceptions import NotFoundError


class TaskHandler:
    """Чтение статуса задачи и истории попыток."""

    async def get(self, session: AsyncSession, task_id: UUID) -> TaskResponse:
        """Получить задачу с историей попыток.

        :param session: сессия БД
        :param task_id: идентификатор задачи
        :return: ответ с задачей
        """
        query = TaskQuery(session)
        try:
            task = await query.get_task_with_attempts(task_id)
        except NotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        # Текущая нога — провайдер текущего leg_id (если ещё есть).
        current_leg: str | None = None
        if task.current_leg_id is not None:
            leg = await query.get_leg(task.current_leg_id)
            if leg is not None:
                current_leg = leg.provider

        attempts = [
            AttemptHistory(
                provider=a.provider,
                status=a.status,
                error_type=a.error_type,
                sent_at=a.sent_at,
                created_at=a.created_at,
            )
            for a in sorted(task.attempts, key=lambda a: a.attempt_no)
        ]
        return TaskResponse(
            id=task.id,
            status=task.status,
            model_slug=task.model_slug,
            price_tokens=task.price_tokens,
            current_leg=current_leg,
            result_url=task.result_url,
            error_type=task.error_type,
            attempts=attempts,
        )
