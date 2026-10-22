"""HMAC-подпись вебхуков.

Подпись вычисляется по СЫРОМУ телу запроса (байты до JSON-парсинга) — это
принципиально: парсинг может изменить порядок ключей/пробелы и сломать подпись.
"""

import hashlib
import hmac


def sign_body(raw_body: bytes, secret: str) -> str:
    """Вычислить HMAC-SHA256 сырого тела и вернуть hex-дайджест.

    :param raw_body: сырые байты тела
    :param secret: общий секрет
    :return: hex-строка подписи
    """
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def verify_signature(raw_body: bytes, secret: str, signature: str | None) -> bool:
    """Проверить подпись сырого тела через сравнение за константное время.

    :param raw_body: сырые байты тела
    :param secret: общий секрет
    :param signature: переданная подпись из заголовка X-Signature
    :return: True, если подпись совпала
    """
    if not signature:
        return False
    expected = sign_body(raw_body, secret)
    return hmac.compare_digest(expected, signature)
