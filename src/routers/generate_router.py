"""Роуты генерации."""

from typing import Annotated

from fastapi import APIRouter, Depends

from src.handlers.generate_handler import GenerateHandler, get_generate_handler
from src.schemas import GenerateRequest, GenerateResponse

router = APIRouter()

GenerateHandlerDep = Annotated[GenerateHandler, Depends(get_generate_handler)]


@router.post(
    "/generate",
    summary="Создание заказа",
    response_model=GenerateResponse,
    status_code=200,
)
async def create_generation(
    data: GenerateRequest,
    handler: GenerateHandlerDep,
) -> GenerateResponse:
    """Принять заказ: списать цену, создать задачу и вернуть task_id."""
    return await handler.create(
        user_id=data.user_id,
        model_slug=data.model_slug,
        prompt=data.prompt,
        ref_images=data.ref_images,
    )
