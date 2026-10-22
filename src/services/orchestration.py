"""Оркестрация бизнес-логики: генерация, маршрутизация, деньги, вебхуки.

Это ядро сервиса. Все функции принимают AsyncSession и работают в транзакции,
которую контролирует вызывающий (handler или фоновый цикл). Денежные операции
защищены уникальными ключами идемпотентности и блокировками строк.
"""

import uuid
from uuid import UUID

from sqlalchemy import func, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from src.database.models import (
    Attempt,
    AttemptStatus,
    ErrorType,
    LedgerEntry,
    LedgerKind,
    Leg,
    Model,
    Payment,
    Task,
    TaskStatus,
    User,
    WebhookEvent,
)
from src.services.queries.catalog_queries import CatalogQuery
from src.services.queries.task_queries import TaskQuery
from src.services.queries.user_queries import UserQuery
from src.schemas import PaymentWebhook, ProviderWebhook
from src.utils.exceptions import (
    ActiveTasksLimitError,
    InsufficientTokensError,
    NoFittingLegError,
    NotFoundError,
)

# Статус платежа, при котором начисляются токены.
PAYMENT_SUCCESS_STATUS = "paid"


def _now():
    """Серверное время Postgres для меток времени (единый источник времени)."""
    return func.now()


def _new_attempt(task: Task, leg: Leg, attempt_no: int) -> Attempt:
    """Создать попытку для задачи через указанную ногу.

    :param task: задача
    :param leg: нога (провайдер)
    :param attempt_no: порядковый номер попытки
    :return: новая попытка в статусе pending
    """
    return Attempt(
        task_id=task.id,
        leg_id=leg.id,
        provider=leg.provider,
        attempt_no=attempt_no,
        attempt_id=str(uuid.uuid4()),
        status=AttemptStatus.PENDING.value,
    )


async def generate(
    session: AsyncSession,
    user_id: UUID,
    model_slug: str,
    prompt: str,
    ref_images: list[str],
) -> Task:
    """Создать заказ: списать цену, создать задачу и первую попытку.

    Порядок проверок (по design.md): 429 (лимит активных) → 402 (баланс) →
    400 (нет подходящей ноги). Списание происходит только после всех проверок.

    :param session: сессия БД
    :param user_id: идентификатор пользователя
    :param model_slug: слаг модели
    :param prompt: промпт
    :param ref_images: список референсных картинок
    :return: созданная задача
    """
    user_query = UserQuery(session)
    task_query = TaskQuery(session)
    catalog_query = CatalogQuery(session)

    # === ШАГ 1: блокировка пользователя (сериализует заказы пользователя) ===
    user = await user_query.lock_user(user_id)
    if user is None:
        raise NotFoundError(f"Пользователь {user_id} не найден")

    # === ШАГ 2: модель и цена ===
    model: Model = await catalog_query.get_model(model_slug)
    price = model.price_tokens

    # === ШАГ 3: лимит активных задач (429) ===
    active = await task_query.count_active_tasks(user_id)
    if active >= settings.ACTIVE_TASKS_LIMIT:
        raise ActiveTasksLimitError(
            f"У пользователя {user_id} уже {active} активных задач"
        )

    # === ШАГ 4: достаточно ли токенов (402) ===
    if user.balance < price:
        raise InsufficientTokensError(
            f"Недостаточно токенов: нужно {price}, есть {user.balance}"
        )

    # === ШАГ 5: подходящая нога (400 до списания) ===
    legs = await catalog_query.get_legs(model_slug)
    ref_count = len(ref_images)
    fitting = [leg for leg in legs if leg.max_ref_images >= ref_count]
    if not fitting:
        raise NoFittingLegError(
            f"Ни одна нога модели {model_slug} не принимает {ref_count} референсов"
        )

    # === ШАГ 6: списание, задача, первая попытка (в одной транзакции) ===
    task = Task(
        user_id=user_id,
        model_slug=model_slug,
        price_tokens=price,
        prompt=prompt,
        ref_images=ref_images,
        ref_images_count=ref_count,
        status=TaskStatus.RUNNING.value,
        current_leg_id=fitting[0].id,
    )
    session.add(task)
    await session.flush()  # получить task.id для строки журнала

    # Списание: отрицательная строка журнала + уменьшение баланса.
    session.add(
        LedgerEntry(
            user_id=user_id,
            kind=LedgerKind.DEBIT.value,
            amount=-price,
            task_id=task.id,
        )
    )
    user.balance -= price

    session.add(_new_attempt(task, fitting[0], 1))
    return task


async def advance(
    session: AsyncSession,
    task: Task,
    failed_attempt: Attempt,
    error_type: str,
) -> None:
    """Продвинуть задачу после отказа попытки.

    Вызывается при уже заблокированной задаче. Решает: следующая нога или
    терминальная неудача с возвратом.

    :param session: сессия БД
    :param task: заблокированная задача
    :param failed_attempt: завершившаяся неудачей попытка
    :param error_type: тип ошибки (technical/moderation/payload/sync_error/timeout)
    """
    # payload означает неверную форму — отвергнет любая нога, сразу в failed.
    if error_type == ErrorType.PAYLOAD.value:
        await fail_task(session, task, error_type)
        return

    legs = await CatalogQuery(session).get_legs(task.model_slug)
    failed_leg = await TaskQuery(session).get_leg(failed_attempt.leg_id)
    current_priority = failed_leg.priority if failed_leg else -1
    moderation = error_type == ErrorType.MODERATION.value

    next_leg: Leg | None = None
    for leg in legs:
        if leg.priority <= current_priority:
            continue
        if leg.max_ref_images < task.ref_images_count:
            continue
        if moderation and leg.skip_on_moderation:
            continue
        next_leg = leg
        break

    if next_leg is None:
        await fail_task(session, task, error_type)
        return

    task.current_leg_id = next_leg.id
    task.status = TaskStatus.RUNNING.value
    task.updated_at = _now()
    session.add(_new_attempt(task, next_leg, failed_attempt.attempt_no + 1))


async def fail_task(session: AsyncSession, task: Task, error_type: str) -> None:
    """Перевести задачу в failed и вернуть токены ровно один раз.

    :param session: сессия БД
    :param task: заблокированная задача
    :param error_type: тип финальной ошибки
    """
    task.status = TaskStatus.FAILED.value
    task.error_type = error_type
    task.updated_at = _now()
    await _apply_refund(session, task)


async def _apply_refund(session: AsyncSession, task: Task) -> bool:
    """Вернуть цену задачи на баланс (ровно один раз).

    Уникальный частичный индекс (task_id, kind) гарантирует, что возврат по
    задаче создаётся единожды даже при гонке. Баланс обновляется только если
    строка возврата реально вставлена.

    :param session: сессия БД
    :param task: задача
    :return: True, если возврат был выполнен сейчас
    """
    stmt = (
        pg_insert(LedgerEntry)
        .values(
            user_id=task.user_id,
            kind=LedgerKind.REFUND.value,
            amount=task.price_tokens,
            task_id=task.id,
        )
        .on_conflict_do_nothing(
            index_elements=["task_id", "kind"],
            index_where=text("kind IN ('debit', 'refund')"),
        )
    )
    result = await session.execute(stmt)
    if result.rowcount == 0:
        return False
    await session.execute(
        update(User)
        .where(User.id == task.user_id)
        .values(balance=User.balance + task.price_tokens)
    )
    return True


async def handle_provider_webhook(
    session: AsyncSession,
    provider: str,
    payload: ProviderWebhook,
) -> str:
    """Обработать вебхук провайдера (completed/failed).

    Возвращает строковый код исхода для ответа/логирования.

    :param session: сессия БД
    :param provider: имя провайдера (alpha/beta)
    :param payload: распарсенное тело вебхука
    :return: код исхода (completed/failed/duplicate/late/ignored/unknown)
    """
    # === ШАГ 1: дедуп по (provider, event_id) ===
    try:
        session.add(
            WebhookEvent(
                provider=provider,
                event_id=payload.event_id,
                task_id=payload.task_id,
                attempt_id=payload.attempt_id,
                payload_status=payload.result,
                error_type=payload.error_type,
            )
        )
        await session.flush()
    except IntegrityError:
        # Повторная доставка одного и того же события — игнорируем.
        await session.rollback()
        return "duplicate"

    task_query = TaskQuery(session)

    # === ШАГ 2: поиск попытки по attempt_id ===
    attempt = await task_query.get_attempt_by_attempt_id(payload.attempt_id)
    if attempt is None:
        await session.commit()
        return "unknown"

    # Defense-in-depth: событие должно приходить от провайдера этой попытки.
    if attempt.provider != provider:
        await session.commit()
        return "ignored"

    # === ШАГ 3: блокировка задачи, затем попытки (фиксированный порядок) ===
    task = await task_query.lock_task(attempt.task_id)
    if task is None:
        await session.commit()
        return "unknown"
    locked_attempt = await task_query.lock_attempt(attempt.id)
    if locked_attempt is None:
        await session.commit()
        return "unknown"

    # === ШАГ 4: сторож уже снял попытку → поздний completed (правило 6) ===
    if locked_attempt.status == AttemptStatus.TIMEOUT.value:
        if payload.result == "completed":
            locked_attempt.status = AttemptStatus.LATE_COMPLETED.value
            locked_attempt.result_url = payload.file_url
            locked_attempt.updated_at = _now()
        await session.commit()
        return "late"

    # === ШАГ 5: попытка уже терминальна → игнор ===
    if locked_attempt.status in (
        AttemptStatus.SUCCEEDED.value,
        AttemptStatus.FAILED.value,
        AttemptStatus.LATE_COMPLETED.value,
    ):
        await session.commit()
        return "ignored"

    if locked_attempt.status != AttemptStatus.SENT.value:
        await session.commit()
        return "ignored"

    # === ШАГ 6: задача терминальна (защита) ===
    if task.status in (TaskStatus.SUCCEEDED.value, TaskStatus.FAILED.value):
        await session.commit()
        return "late"

    # === ШАГ 7: обработка результата ===
    if payload.result == "completed":
        locked_attempt.status = AttemptStatus.SUCCEEDED.value
        locked_attempt.result_url = payload.file_url
        locked_attempt.updated_at = _now()
        task.status = TaskStatus.SUCCEEDED.value
        task.result_url = payload.file_url
        task.updated_at = _now()
        await session.commit()
        return "completed"

    if payload.result == "failed":
        error_type = payload.error_type or ErrorType.TECHNICAL.value
        locked_attempt.status = AttemptStatus.FAILED.value
        locked_attempt.error_type = error_type
        locked_attempt.updated_at = _now()
        await advance(session, task, locked_attempt, error_type)
        await session.commit()
        return "failed"

    await session.commit()
    return "ignored"


async def handle_payment_webhook(
    session: AsyncSession,
    payload: PaymentWebhook,
) -> str:
    """Обработать вебхук платежа (начисление ровно один раз).

    :param session: сессия БД
    :param payload: распарсенное тело вебхука
    :return: код исхода (credited/duplicate/ignored)
    """
    # === ШАГ 1: upsert записи о платеже (не расходуем payment_id зря) ===
    # Промежуточный статус (pending и т.п.) не должен «съедать» payment_id:
    # при повторном вебхуке с тем же payment_id и статусом paid строка
    # обновляется, а начисление защищено partial unique (payment_id, kind).
    stmt = (
        pg_insert(Payment)
        .values(
            payment_id=payload.payment_id,
            user_id=payload.user_id,
            tokens=payload.tokens,
            status=payload.status,
        )
        .on_conflict_do_update(
            index_elements=["payment_id"],
            set_={"status": payload.status, "tokens": payload.tokens},
        )
    )
    await session.execute(stmt)

    # === ШАГ 2: начисляем только при успешном статусе ===
    if payload.status != PAYMENT_SUCCESS_STATUS:
        await session.commit()
        return "ignored"

    credited = await _apply_topup(
        session, payload.user_id, payload.tokens, payload.payment_id
    )
    await session.commit()
    return "credited" if credited else "duplicate"


async def _apply_topup(
    session: AsyncSession,
    user_id: UUID,
    tokens: int,
    payment_id: str,
) -> bool:
    """Начислить токены (ровно один раз по payment_id).

    :param session: сессия БД
    :param user_id: идентификатор пользователя
    :param tokens: сумма начисления
    :param payment_id: идентификатор платежа
    :return: True, если начисление выполнено сейчас
    """
    stmt = (
        pg_insert(LedgerEntry)
        .values(
            user_id=user_id,
            kind=LedgerKind.TOPUP.value,
            amount=tokens,
            payment_id=payment_id,
        )
        .on_conflict_do_nothing(
            index_elements=["payment_id", "kind"],
            index_where=text("kind = 'topup'"),
        )
    )
    result = await session.execute(stmt)
    if result.rowcount == 0:
        return False
    await session.execute(
        update(User).where(User.id == user_id).values(balance=User.balance + tokens)
    )
    return True
