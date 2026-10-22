"""Точка входа FastAPI-приложения сервиса генерации."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from config import settings
from src.routers import generate_router, task_router, user_router, webhook_router
from src.services.dispatch import dispatcher_loop
from src.services.provider_client import ProviderClient
from src.services.watchdog import watchdog_loop

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Жизненный цикл: клиенты провайдеров и фоновые циклы.

    Диспетчер и сторож запускаются в КАЖДОМ воркере. Корректность при этом
    обеспечена блокировками БД, а не состоянием в памяти процесса.
    """
    if settings.OTEL_ENABLED:
        from src.telemetry.setup_telemetry import instrument_app, setup_telemetry

        setup_telemetry("generation-service", settings.OTEL_ENDPOINT)
        instrument_app(app)

    app.state.provider_client = ProviderClient()
    background_tasks = [
        asyncio.create_task(dispatcher_loop(app)),
        asyncio.create_task(watchdog_loop(app)),
    ]

    yield

    for task in background_tasks:
        task.cancel()
    await asyncio.gather(*background_tasks, return_exceptions=True)
    await app.state.provider_client.close()


def create_app() -> FastAPI:
    """Создать экземпляр приложения.

    :return: сконфигурированное FastAPI-приложение
    """
    app = FastAPI(title="Generation Service", lifespan=lifespan)
    app.include_router(generate_router)
    app.include_router(webhook_router)
    app.include_router(task_router)
    app.include_router(user_router)
    return app


app = create_app()
