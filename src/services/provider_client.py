"""HTTP-клиент для фейковых провайдеров alpha/beta."""

from dataclasses import dataclass

import httpx

from config import settings


@dataclass
class ProviderResult:
    """Результат синхронного вызова POST /tasks провайдера."""

    ok: bool
    external_id: str | None = None
    status_code: int | None = None


class ProviderClient:
    """Клиент отправки задач провайдерам.

    Клиенты создаются в lifespan и хранятся в app.state; здесь — объект с
    одним пулом на провайдера.
    """

    def __init__(self) -> None:
        timeout = httpx.Timeout(
            connect=3.0, read=settings.PROVIDER_CALL_TIMEOUT, write=3.0, pool=2.0
        )
        limits = httpx.Limits(
            max_connections=10, max_keepalive_connections=5, keepalive_expiry=30
        )
        self._clients: dict[str, httpx.AsyncClient] = {
            "alpha": httpx.AsyncClient(
                base_url=settings.ALPHA_BASE_URL, timeout=timeout, limits=limits
            ),
            "beta": httpx.AsyncClient(
                base_url=settings.BETA_BASE_URL, timeout=timeout, limits=limits
            ),
        }

    def callback_url(self, provider: str) -> str:
        """URL, на который провайдер должен прислать вебхук.

        :param provider: имя провайдера
        :return: полный URL вебхука сервиса
        """
        return f"{settings.SERVICE_BASE_URL}/webhooks/providers/{provider}"

    async def post_task(
        self,
        provider: str,
        attempt_id: str,
        task_id: str,
        model_slug: str,
        prompt: str,
        ref_images: list,
    ) -> ProviderResult:
        """Отправить задачу провайдеру.

        Синхронные ошибки (5xx, таймаут) возвращаются как ProviderResult(ok=False),
        чтобы вызывающий код вёл задачу на следующую ногу.

        :param provider: имя провайдера
        :param attempt_id: публичный attempt_id (идемпотентность провайдера)
        :param task_id: идентификатор задачи
        :param model_slug: слаг модели
        :param prompt: промпт
        :param ref_images: список референсов
        :return: результат вызова
        """
        body = {
            "attempt_id": attempt_id,
            "task_id": task_id,
            "model_slug": model_slug,
            "prompt": prompt,
            "ref_images": ref_images,
            "callback_url": self.callback_url(provider),
        }
        try:
            response = await self._clients[provider].post("/tasks", json=body)
        except (httpx.TimeoutException, httpx.RequestError):
            return ProviderResult(ok=False)

        if response.status_code == 202:
            data = response.json()
            return ProviderResult(
                ok=True, external_id=data.get("external_id"), status_code=202
            )
        return ProviderResult(ok=False, status_code=response.status_code)

    async def close(self) -> None:
        """Закрыть клиенты и освободить ресурсы."""
        for client in self._clients.values():
            await client.aclose()
