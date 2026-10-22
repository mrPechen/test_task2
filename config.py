"""Конфигурация сервиса генерации.

Все параметры читаются из переменных окружения (или .env) и замораживаются
после создания объекта настроек — так конфигурация не меняется в рантайме.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки приложения.

    :ivar DEBUG: режим отладки (влияет на включение телеметрии)
    :ivar HOST: адрес, на котором слушает сервис
    :ivar PORT: порт сервиса
    :ivar DATABASE_URL: строка подключения к PostgreSQL (async)
    :ivar WEBHOOK_SECRET: общий секрет для HMAC-подписи вебхуков
    :ivar PROVIDER_TIMEOUT: сколько ждать вебхук от провайдера до срабатывания сторожа
    :ivar PROVIDER_CALL_TIMEOUT: таймаут HTTP-запроса к провайдеру
    :ivar ACTIVE_TASKS_LIMIT: максимум активных задач на пользователя
    :ivar WATCHDOG_INTERVAL: период опроса сторожа
    :ivar DISPATCH_INTERVAL: период опроса диспетчера
    :ivar ALPHA_BASE_URL: базовый URL провайдера alpha
    :ivar BETA_BASE_URL: базовый URL провайдера beta
    :ivar SERVICE_BASE_URL: URL сервиса, куда провайдеры шлют вебхуки
    :ivar OTEL_ENABLED: включать ли OpenTelemetry
    :ivar OTEL_ENDPOINT: OTLP-эндпоинт для телеметрии
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        frozen=True,
    )

    # App
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # DB
    DATABASE_URL: str = (
        "postgresql+asyncpg://generation:generation@localhost:5432/generation"
    )

    # Security
    WEBHOOK_SECRET: str = "change-me"

    # Timeouts and limits
    PROVIDER_TIMEOUT: int = 60
    PROVIDER_CALL_TIMEOUT: int = 10
    ACTIVE_TASKS_LIMIT: int = 3

    # Background loops
    WATCHDOG_INTERVAL: float = 1.0
    DISPATCH_INTERVAL: float = 1.0

    # Providers
    ALPHA_BASE_URL: str = "http://alpha:8001"
    BETA_BASE_URL: str = "http://beta:8002"
    SERVICE_BASE_URL: str = "http://app:8000"

    # Telemetry
    OTEL_ENABLED: bool = False
    OTEL_ENDPOINT: str = ""


settings = Settings()
