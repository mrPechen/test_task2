"""Фейковый провайдер: принимает POST /tasks, шлёт подписанные вебхуки.

Поведение настраивается через env или PUT /_settings (для тестов), чтобы
воспроизводить: отказы, дубли, потерю вебхуков, поздний completed, синхронные
ошибки.
"""

import asyncio
import json
import logging
import random
import uuid

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

from fake_provider.config import ProviderSettings
from fake_provider.signature import sign_body

logger = logging.getLogger(__name__)

# Каноничная сериализация тела вебхука: без пробелов, чтобы подпись совпадала.
_JSON_SEPARATORS = (",", ":")


class Behavior(BaseModel):
    """Рантайм-настройки поведения провайдера (изменяются через /_settings)."""

    failure_rate: float = 0.0
    error_type: str = "technical"
    sync_failure: str = "none"
    duplicate_webhook: bool = False
    drop_webhook: bool = False
    late_completed: bool = False
    delay_min: float = 2.0
    delay_max: float = 20.0
    late_delay: float = 65.0
    sync_timeout_delay: float = 15.0


class TaskRequest(BaseModel):
    """Тело POST /tasks от сервиса генерации."""

    attempt_id: str
    task_id: str
    model_slug: str
    prompt: str
    ref_images: list[str] = Field(default_factory=list)
    callback_url: str


def _build_behavior(settings: ProviderSettings) -> Behavior:
    """Собрать поведение из env-дефолтов."""
    return Behavior(
        failure_rate=settings.FAILURE_RATE,
        error_type=settings.ERROR_TYPE,
        sync_failure=settings.SYNC_FAILURE,
        duplicate_webhook=settings.DUPLICATE_WEBHOOK,
        drop_webhook=settings.DROP_WEBHOOK,
        late_completed=settings.LATE_COMPLETED,
        delay_min=settings.DELAY_MIN,
        delay_max=settings.DELAY_MAX,
        late_delay=settings.LATE_DELAY,
        sync_timeout_delay=settings.SYNC_TIMEOUT_DELAY,
    )


async def _send_webhook(
    client: httpx.AsyncClient, secret: str, callback_url: str, body: dict
) -> None:
    """Отправить подписанный вебхук.

    :param client: HTTPX-клиент
    :param secret: общий секрет
    :param callback_url: URL вебхука сервиса
    :param body: тело вебхука
    """
    raw = json.dumps(body, separators=_JSON_SEPARATORS).encode("utf-8")
    signature = sign_body(raw, secret)
    try:
        await client.post(
            callback_url,
            content=raw,
            headers={"Content-Type": "application/json", "X-Signature": signature},
        )
    except httpx.RequestError:
        logger.warning("Не удалось доставить вебхук на %s", callback_url)


def _result_body(
    attempt_id: str,
    task_id: str,
    external_id: str,
    behavior: Behavior,
    forced_failure: bool,
) -> dict:
    """Сформировать тело вебхука completed/failed.

    :param attempt_id: идентификатор попытки
    :param task_id: идентификатор задачи
    :param external_id: external_id провайдера
    :param behavior: текущее поведение
    :param forced_failure: завершить задачу отказом (по доле отказов)
    :return: словарь тела вебхука
    """
    if forced_failure:
        return {
            "event_id": str(uuid.uuid4()),
            "attempt_id": attempt_id,
            "task_id": task_id,
            "external_id": external_id,
            "result": "failed",
            "error_type": behavior.error_type,
            "file_url": None,
        }
    return {
        "event_id": str(uuid.uuid4()),
        "attempt_id": attempt_id,
        "task_id": task_id,
        "external_id": external_id,
        "result": "completed",
        "error_type": None,
        "file_url": f"http://files/{external_id}.png",
    }


def create_app(settings: ProviderSettings) -> FastAPI:
    """Создать приложение фейкового провайдера.

    :param settings: настройки провайдера
    :return: FastAPI-приложение
    """
    app = FastAPI(title=f"Fake provider {settings.PROVIDER_NAME}")
    router = APIRouter()

    # Состояние: рантайм-поведение + отображение attempt_id -> external_id.
    state = {
        "behavior": _build_behavior(settings),
        "attempts": {},  # attempt_id -> external_id
        "tasks": set(),  # фоновые asyncio-задачи (удерживаем ссылки от GC)
    }
    app.state.provider_state = state

    client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))

    async def _schedule(
        attempt_id: str, task_id: str, external_id: str, callback_url: str
    ) -> None:
        """Запланировать отложенную отправку вебхука(ов) согласно поведению.

        :param attempt_id: идентификатор попытки
        :param task_id: идентификатор задачи
        :param external_id: external_id провайдера
        :param callback_url: URL вебхука сервиса
        """
        behavior: Behavior = state["behavior"]

        if behavior.drop_webhook:
            return

        if behavior.late_completed:
            delay = behavior.late_delay
            forced = False  # поздний completed всегда успешен
        else:
            delay = random.uniform(behavior.delay_min, behavior.delay_max)
            forced = random.random() < behavior.failure_rate

        body = _result_body(attempt_id, task_id, external_id, behavior, forced)

        async def send() -> None:
            await asyncio.sleep(delay)
            await _send_webhook(client, settings.WEBHOOK_SECRET, callback_url, body)

        if behavior.duplicate_webhook:
            # Две одинаковые доставки (одно event_id) — в т.ч. одновременно.
            task1 = asyncio.create_task(send())
            task2 = asyncio.create_task(send())
            state["tasks"].add(task1)
            state["tasks"].add(task2)
        else:
            task = asyncio.create_task(send())
            state["tasks"].add(task)

    @router.post("/tasks")
    async def create_task(req: TaskRequest) -> Response:
        """Принять задачу и ответить 202 с external_id."""
        behavior: Behavior = state["behavior"]

        # Синхронный отказ до ответа.
        if behavior.sync_failure == "503":
            return Response(status_code=503, content="service unavailable")
        if behavior.sync_failure == "timeout":
            await asyncio.sleep(behavior.sync_timeout_delay)

        # Идемпотентность по attempt_id: повторный запрос не создаёт новую задачу.
        if req.attempt_id in state["attempts"]:
            external_id = state["attempts"][req.attempt_id]
            return Response(
                status_code=202,
                content=json.dumps(
                    {"external_id": external_id}, separators=_JSON_SEPARATORS
                ),
                media_type="application/json",
            )

        external_id = str(uuid.uuid4())
        state["attempts"][req.attempt_id] = external_id

        # Планируем вебхук (без ожидания ответа).
        asyncio.create_task(
            _schedule(req.attempt_id, req.task_id, external_id, req.callback_url)
        )

        return Response(
            status_code=202,
            content=json.dumps(
                {"external_id": external_id}, separators=_JSON_SEPARATORS
            ),
            media_type="application/json",
        )

    @router.put("/_settings")
    async def update_settings(behavior: Behavior) -> dict:
        """Обновить поведение (только при ADMIN_ENABLED=true)."""
        if not settings.ADMIN_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        state["behavior"] = behavior
        return {"status": "ok", "behavior": behavior.model_dump()}

    @router.post("/_reset")
    async def reset() -> dict:
        """Сбросить состояние (только при ADMIN_ENABLED=true)."""
        if not settings.ADMIN_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        state["attempts"] = {}
        return {"status": "ok"}

    app.include_router(router)
    return app


def _settings() -> ProviderSettings:
    return ProviderSettings()


app = create_app(_settings())
