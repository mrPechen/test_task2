"""Handler вебхуков провайдеров и платежей."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.schemas import PaymentWebhook, ProviderWebhook
from src.services.orchestration import handle_payment_webhook, handle_provider_webhook


class WebhookHandler:
    """Обработка вебхуков (подпись проверяется в роутере до вызова)."""

    async def handle_provider(
        self,
        session: AsyncSession,
        provider: str,
        payload: ProviderWebhook,
    ) -> str:
        """Обработать вебхук провайдера.

        :param session: сессия БД
        :param provider: имя провайдера
        :param payload: тело вебхука
        :return: код исхода
        """
        return await handle_provider_webhook(session, provider, payload)

    async def handle_payment(
        self,
        session: AsyncSession,
        payload: PaymentWebhook,
    ) -> str:
        """Обработать вебхук платежа.

        :param session: сессия БД
        :param payload: тело вебхука
        :return: код исхода
        """
        return await handle_payment_webhook(session, payload)
