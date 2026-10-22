"""Диспетчер: отправка попыток провайдерам.

Попытка сначала создаётся в БД со статусом pending, а диспетчер забирает её и
отправляет. Это даёт восстановление после падения процесса: если воркер упал
после создания попытки, другой воркер подхватит её на следующем проходе.
"""

import asyncio
import logging

from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from src.database.db_settings import SessionLocal
from src.database.models import Attempt, AttemptStatus, ErrorType, Task, TaskStatus
from src.services.orchestration import advance
from src.services.provider_client import ProviderClient
from src.services.queries.task_queries import TaskQuery

logger = logging.getLogger(__name__)

# Терминальные статусы задачи.
_TERMINAL = (TaskStatus.SUCCEEDED.value, TaskStatus.FAILED.value)


async def dispatch_once(session: AsyncSession, provider_client: ProviderClient) -> bool:
    """Обработать одну pending-попытку.

    :param session: сессия БД
    :param provider_client: клиент провайдеров
    :return: True, если была обработана хотя бы одна попытка
    """
    task_query = TaskQuery(session)

    # === ШАГ 1: найти pending-попытку активной задачи ===
    attempt = (
        await session.execute(
            select(Attempt)
            .join(Task, Task.id == Attempt.task_id)
            .where(
                Attempt.status == AttemptStatus.PENDING.value,
                Task.status.not_in(_TERMINAL),
            )
            .order_by(Attempt.created_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if attempt is None:
        return False

    # === ШАГ 2: заблокировать задачу, затем попытку, пометить sent ===
    task = await task_query.lock_task(attempt.task_id)
    locked = await task_query.lock_attempt(attempt.id)
    if task is None or locked is None or task.status in _TERMINAL:
        await session.commit()
        return True
    if locked.status != AttemptStatus.PENDING.value:
        await session.commit()
        return True

    locked.status = AttemptStatus.SENT.value
    locked.sent_at = func.now()  # серверное время — единый источник со сторожем
    locked.updated_at = func.now()
    # Фиксируем значения ДО commit: после commit объекты expired, и доступ к
    # атрибутам вызвал бы lazy-load вне greenlet-контекста.
    provider = locked.provider
    attempt_public_id = locked.attempt_id
    attempt_db_id = locked.id
    task_id = task.id
    model_slug = task.model_slug
    prompt = task.prompt
    ref_images = task.ref_images
    await session.commit()  # durable-претензия; блокировки освобождены

    # === ШАГ 3: HTTP-вызов вне транзакции ===
    result = await provider_client.post_task(
        provider, attempt_public_id, str(task_id), model_slug, prompt, ref_images
    )

    # === ШАГ 4: обработать результат под блокировкой ===
    task2 = await task_query.lock_task(task_id)
    attempt2 = await task_query.lock_attempt(attempt_db_id)
    if task2 is None or attempt2 is None:
        await session.commit()
        return True

    # Сторож мог снять попытку за время HTTP-вызова, либо задача ушла дальше.
    if attempt2.status != AttemptStatus.SENT.value or task2.status in _TERMINAL:
        if result.ok:
            attempt2.external_id = result.external_id
            attempt2.updated_at = func.now()
        await session.commit()
        return True

    if result.ok:
        attempt2.external_id = result.external_id
        attempt2.updated_at = func.now()
        await session.commit()
    else:
        attempt2.status = AttemptStatus.FAILED.value
        attempt2.error_type = ErrorType.SYNC_ERROR.value
        attempt2.updated_at = func.now()
        await advance(session, task2, attempt2, ErrorType.SYNC_ERROR.value)
        await session.commit()
    return True


async def dispatcher_loop(app: FastAPI) -> None:
    """Фоновый цикл диспетчера (запускается в lifespan каждого воркера)."""
    provider_client: ProviderClient = app.state.provider_client
    logger.info("Диспетчер запущен")
    while True:
        try:
            async with SessionLocal() as session:
                await dispatch_once(session, provider_client)
        except Exception:
            logger.exception("Ошибка в цикле диспетчера")
        await asyncio.sleep(settings.DISPATCH_INTERVAL)
