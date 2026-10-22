"""Чистые запросы к БД: каталог моделей и ноги."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import Leg, Model
from src.utils.exceptions import NotFoundError


class CatalogQuery:
    """Запросы к каталогу моделей и ногам."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_model(self, slug: str) -> Model:
        """Получить модель по slug.

        :param slug: идентификатор модели
        :return: модель
        :raises NotFoundError: если модели нет
        """
        model = (
            await self.session.execute(select(Model).where(Model.slug == slug))
        ).scalar_one_or_none()
        if model is None:
            raise NotFoundError(f"Модель {slug} не найдена")
        return model

    async def get_legs(self, model_slug: str) -> list[Leg]:
        """Получить ноги модели в порядке приоритета.

        :param model_slug: идентификатор модели
        :return: список ног, отсортированный по приоритету
        """
        result = await self.session.execute(
            select(Leg).where(Leg.model_slug == model_slug).order_by(Leg.priority.asc())
        )
        return list(result.scalars().all())
