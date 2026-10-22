"""Бизнес-исключения сервиса генерации."""


class NotFoundError(Exception):
    """Сущность с указанным значением не найдена."""

    def __init__(self, value: str) -> None:
        self.value = value
        super().__init__(value)


class AlreadyExistsError(Exception):
    """Сущность с таким значением уже существует."""

    def __init__(self, value: str) -> None:
        self.value = value
        super().__init__(value)


class InsufficientTokensError(Exception):
    """Недостаточно токенов на балансе пользователя."""


class ActiveTasksLimitError(Exception):
    """Превышен лимит активных задач пользователя."""


class NoFittingLegError(Exception):
    """Ни одна нога модели не принимает переданное число референсов."""
