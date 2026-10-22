"""Сторож: снятие попыток по таймауту провайдера.

Каждый воркер запускает свой цикл сторожа. Корректность обеспечивается не
SKIP LOCKED, а перепроверкой статуса под блокировкой задачи и попытки:
первый воркер переводит попытку в timeout и продвигает задачу, остальные видят
уже изменённый статус и пропускают.
"""

import asyncio
import logging

from fastapi import FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from src.database.db_settings import SessionLocal
from src.database.models import Attempt, AttemptStatus, ErrorType, Task, TaskStatus
from src.services.orchestration import advance
from src.services.queries.task_queries import TaskQuery

logger = logging.getLogger(__name__)

_TERMINAL = (TaskStatus.SUCCEEDED.value, TaskStatus.FAILED.value)


async def watchdog_once(session: AsyncSession) -> int:
    """Снять все истёкшие попытки и продвинуть задачи.

    :param session: сессия БД
    :return: число обработанных попыток
    """
    # Отсечка считается в SQL (now() — из Postgres), чтобы не зависеть от часов воркеров.
    interval = text(f"make_interval(secs => {settings.PROVIDER_TIMEOUT})")
    due = (
        (
            await session.execute(
                select(Attempt).where(
                    Attempt.status == AttemptStatus.SENT.value,
                    Attempt.sent_at <= func.now() - interval,
                )
            )
        )
        .scalars()
        .all()
    )

    task_query = TaskQuery(session)
    processed = 0
    for attempt in due:
        task = await task_query.lock_task(attempt.task_id)
        if task is None or task.status in _TERMINAL:
            continue
        locked = await task_query.lock_attempt(attempt.id)
        if locked is None or locked.status != AttemptStatus.SENT.value:
            continue
        locked.status = AttemptStatus.TIMEOUT.value
        locked.error_type = ErrorType.TIMEOUT.value
        locked.updated_at = func.now()
        await advance(session, task, locked, ErrorType.TIMEOUT.value)
        processed += 1

    if processed:
        await session.commit()
    return processed


async def watchdog_loop(app: FastAPI) -> None:
    """Фоновый цикл сторожа (запускается в lifespan каждого воркера)."""
    logger.info("Сторож запущен")
    while True:
        try:
            async with SessionLocal() as session:
                await watchdog_once(session)
        except Exception:
            logger.exception("Ошибка в цикле сторожа")
        await asyncio.sleep(settings.WATCHDOG_INTERVAL)
