"""HMAC-подпись вебхуков фейкового провайдера (зеркалит src/services/signature.py)."""

import hashlib
import hmac


def sign_body(raw_body: bytes, secret: str) -> str:
    """Вычислить HMAC-SHA256 сырого тела.

    :param raw_body: сырые байты тела
    :param secret: общий секрет
    :return: hex-подпись
    """
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
