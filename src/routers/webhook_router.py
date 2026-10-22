"""Роуты вебхуков (провайдеры и платежи).

Подпись проверяется по СЫРОМУ телу до парсинга JSON — это принципиально для
корректности HMAC.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from src.dependencies.db import get_async_session
from src.handlers.webhook_handler import WebhookHandler
from src.schemas import PaymentWebhook, ProviderWebhook
from src.services.signature import verify_signature

router = APIRouter()

_webhook_handler = WebhookHandler()


@router.post("/webhooks/providers/{name}", summary="Вебхук провайдера", status_code=200)
async def provider_webhook(
    name: str,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Принять результат от провайдера (completed/failed)."""
    body = await request.body()
    if not verify_signature(
        body, settings.WEBHOOK_SECRET, request.headers.get("X-Signature")
    ):
        raise HTTPException(status_code=401, detail="Invalid signature")
    payload = ProviderWebhook.model_validate_json(body)
    result = await _webhook_handler.handle_provider(session, name, payload)
    # Дубликаты и игнор — тоже 200, чтобы провайдер не ретраил бесконечно.
    return {"status": result}


@router.post("/webhooks/payments", summary="Вебхук платежа", status_code=200)
async def payment_webhook(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Принять уведомление об оплате (идемпотентно по payment_id)."""
    body = await request.body()
    if not verify_signature(
        body, settings.WEBHOOK_SECRET, request.headers.get("X-Signature")
    ):
        raise HTTPException(status_code=401, detail="Invalid signature")
    payload = PaymentWebhook.model_validate_json(body)
    result = await _webhook_handler.handle_payment(session, payload)
    return {"status": result}
