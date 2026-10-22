"""Unit of Work для bounded context генерации."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.services.queries.catalog_queries import CatalogQuery
from src.services.queries.task_queries import TaskQuery
from src.services.queries.user_queries import UserQuery
from src.services.uow.base import AbstractUnitOfWork


class GenerationUnitOfWork(AbstractUnitOfWork):
    """UoW, привязывающий query-классы к сессии."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.user = UserQuery(session)
        self.task = TaskQuery(session)
        self.catalog = CatalogQuery(session)
