"""Реэкспорт роутеров."""

from src.routers.generate_router import router as generate_router
from src.routers.task_router import router as task_router
from src.routers.user_router import router as user_router
from src.routers.webhook_router import router as webhook_router

__all__ = ["generate_router", "task_router", "user_router", "webhook_router"]
