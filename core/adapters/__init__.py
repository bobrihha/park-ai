"""
Адаптеры для платформ.

Каждый адаптер преобразует платформо-специфичные данные
в универсальный формат MessageService и обратно.
"""

from core.adapters.telegram_adapter import TelegramAdapter, get_telegram_adapter

__all__ = [
    "TelegramAdapter",
    "get_telegram_adapter",
]
