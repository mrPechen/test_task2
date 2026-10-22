"""Handler создания заказа на генерацию."""

from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException

from src.database.db_settings import SessionLocal
from src.schemas import GenerateResponse
from src.services.orchestration import generate
from src.services.uow.generation_uow import GenerationUnitOfWork
from src.utils.exceptions import (
    ActiveTasksLimitError,
    InsufficientTokensError,
    NoFittingLegError,
    NotFoundError,
)


class GenerateHandler:
    """Оркестрация создания заказа и маппинг бизнес-ошибок на HTTP."""

    def __init__(self, uow: GenerationUnitOfWork) -> None:
        self.uow = uow

    async def create(
        self,
        user_id,
        model_slug: str,
        prompt: str,
        ref_images: list[str],
    ) -> GenerateResponse:
        """Создать заказ и вернуть task_id.

        :param user_id: идентификатор пользователя
        :param model_slug: слаг модели
        :param prompt: промпт
        :param ref_images: список референсов
        :return: ответ с task_id
        """
        try:
            task = await generate(
                self.uow.session, user_id, model_slug, prompt, ref_images
            )
        except NotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ActiveTasksLimitError as e:
            raise HTTPException(status_code=429, detail=str(e)) from e
        except InsufficientTokensError as e:
            raise HTTPException(status_code=402, detail=str(e)) from e
        except NoFittingLegError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return GenerateResponse(task_id=task.id)


async def get_generate_uow() -> AsyncGenerator[GenerationUnitOfWork, None]:
    """Фабрика UoW для handler-а генерации."""
    async with SessionLocal() as session:
        async with GenerationUnitOfWork(session) as uow:
            yield uow


async def get_generate_handler(
    uow: GenerationUnitOfWork = Depends(get_generate_uow),
) -> GenerateHandler:
    """Depends-фабрика handler-а генерации."""
    return GenerateHandler(uow)
