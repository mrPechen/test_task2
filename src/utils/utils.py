"""Вспомогательные функции."""

from typing import NoReturn

from fastapi import HTTPException


async def raise_http_exception(status_code: int, detail: str | dict) -> NoReturn:
    """Вызвать HTTPException.

    :param status_code: код ответа
    :param detail: тело ответа
    :return: NoReturn
    :raises HTTPException: всегда
    """
    raise HTTPException(status_code=status_code, detail=detail)
