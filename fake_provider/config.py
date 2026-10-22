"""Конфигурация фейкового провайдера."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderSettings(BaseSettings):
    """Настройки фейкового провайдера.

    Поведение задаётся env-дефолтами и переопределяется в рантайме через
    PUT /_settings (только при ADMIN_ENABLED=true).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        frozen=True,
    )

    PROVIDER_NAME: str = "alpha"
    WEBHOOK_SECRET: str = "change-me"
    ADMIN_ENABLED: bool = False

    # Поведение
    FAILURE_RATE: float = 0.0
    ERROR_TYPE: str = "technical"
    SYNC_FAILURE: str = "none"  # none | 503 | timeout
    DUPLICATE_WEBHOOK: bool = False
    DROP_WEBHOOK: bool = False
    LATE_COMPLETED: bool = False

    # Задержки (сек)
    DELAY_MIN: float = 2.0
    DELAY_MAX: float = 20.0
    LATE_DELAY: float = 65.0
    SYNC_TIMEOUT_DELAY: float = 15.0
