"""
Birthday Flow Module — чистая архитектура для бронирования дней рождений.

Модуль включает:
- BirthdayState — состояния flow
- BirthdayStateMachine — управление переходами
- BookingChecker — проверка бронирований в AmoCRM
- BirthdayFlowHandler — обработка сообщений и callback
"""

from core.birthday.state_machine import BirthdayState, BirthdayStateMachine, get_state_machine
from core.birthday.booking_checker import BookingChecker, BookingStatus
from core.birthday.handlers import BirthdayFlowHandler, FlowResponse, birthday_flow_handler
from core.birthday.messages import MESSAGES, contains_birthday_trigger, BIRTHDAY_TRIGGERS

# Telegram integration
from core.birthday.telegram_integration import (
    handle_birthday_trigger,
    handle_birthday_message,
    handle_birthday_callback,
    is_birthday_flow_active,
    check_birthday_trigger,
    is_birthday_callback,
)

__all__ = [
    # Core
    "BirthdayState",
    "BirthdayStateMachine",
    "get_state_machine",
    "BookingChecker",
    "BookingStatus",
    "BirthdayFlowHandler",
    "FlowResponse",
    "birthday_flow_handler",
    "MESSAGES",
    "contains_birthday_trigger",
    "BIRTHDAY_TRIGGERS",
    # Telegram
    "handle_birthday_trigger",
    "handle_birthday_message",
    "handle_birthday_callback",
    "is_birthday_flow_active",
    "check_birthday_trigger",
    "is_birthday_callback",
]
