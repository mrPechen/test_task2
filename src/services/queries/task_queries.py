"""Чистые запросы к БД: задачи и попытки."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.database.models import Attempt, Leg, Task, TaskStatus
from src.utils.exceptions import NotFoundError

# Статусы, считающиеся активными (занимают слот лимита пользователя).
_ACTIVE_STATUSES = (TaskStatus.PENDING.value, TaskStatus.RUNNING.value)


class TaskQuery:
    """Запросы к задачам и попыткам."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_task(self, task_id: UUID) -> Task:
        """Получить задачу по id.

        :param task_id: идентификатор задачи
        :return: задача
        :raises NotFoundError: если задачи нет
        """
        task = (
            await self.session.execute(select(Task).where(Task.id == task_id))
        ).scalar_one_or_none()
        if task is None:
            raise NotFoundError(f"Задача {task_id} не найдена")
        return task

    async def get_task_with_attempts(self, task_id: UUID) -> Task:
        """Получить задачу вместе с историей попыток.

        :param task_id: идентификатор задачи
        :return: задача с загруженными попытками
        :raises NotFoundError: если задачи нет
        """
        task = (
            await self.session.execute(
                select(Task)
                .where(Task.id == task_id)
                .options(selectinload(Task.attempts))
            )
        ).scalar_one_or_none()
        if task is None:
            raise NotFoundError(f"Задача {task_id} не найдена")
        return task

    async def count_active_tasks(self, user_id: UUID) -> int:
        """Посчитать активные задачи пользователя.

        Вызывается при заблокированной строке пользователя, поэтому результат
        консистентен (все генерации пользователя сериализованы).

        :param user_id: идентификатор пользователя
        :return: число активных задач
        """
        return (
            await self.session.execute(
                select(func.count())
                .select_from(Task)
                .where(Task.user_id == user_id, Task.status.in_(_ACTIVE_STATUSES))
            )
        ).scalar_one()

    async def get_attempt_by_attempt_id(self, attempt_id: str) -> Attempt | None:
        """Найти попытку по публичному attempt_id.

        :param attempt_id: идентификатор попытки, переданный провайдеру
        :return: попытка или None
        """
        return (
            await self.session.execute(
                select(Attempt).where(Attempt.attempt_id == attempt_id)
            )
        ).scalar_one_or_none()

    async def lock_task(self, task_id: UUID) -> Task | None:
        """Заблокировать строку задачи (SELECT FOR UPDATE).

        :param task_id: идентификатор задачи
        :return: задача или None
        """
        return (
            await self.session.execute(
                select(Task).where(Task.id == task_id).with_for_update()
            )
        ).scalar_one_or_none()

    async def lock_attempt(self, attempt_id: UUID) -> Attempt | None:
        """Заблокировать строку попытки (SELECT FOR UPDATE).

        :param attempt_id: идентификатор попытки
        :return: попытка или None
        """
        return (
            await self.session.execute(
                select(Attempt).where(Attempt.id == attempt_id).with_for_update()
            )
        ).scalar_one_or_none()

    async def get_leg(self, leg_id: UUID) -> Leg | None:
        """Получить ногу по id.

        :param leg_id: идентификатор ноги
        :return: нога или None
        """
        return (
            await self.session.execute(select(Leg).where(Leg.id == leg_id))
        ).scalar_one_or_none()
