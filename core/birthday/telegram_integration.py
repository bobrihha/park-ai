"""
Telegram Integration — мост между handlers.py и новым birthday flow.

Этот модуль обеспечивает обратную совместимость:
- Конвертирует Update/Context в параметры для BirthdayFlowHandler
- Конвертирует FlowResponse обратно в Telegram-формат
"""

import logging
from typing import Optional, Tuple
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from core.birthday.handlers import BirthdayFlowHandler, FlowResponse, birthday_flow_handler
from core.birthday.state_machine import BirthdayState, get_state_machine
from core.birthday.messages import contains_birthday_trigger

logger = logging.getLogger(__name__)


async def handle_birthday_trigger(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """
    Обработать триггер birthday flow.
    
    Вызывается при:
    - /birthday команде
    - Кнопке "Организовать праздник"
    - Триггерных словах
    
    Returns:
        True если обработано, False если нужна дальнейшая обработка
    """
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    response = await birthday_flow_handler.handle_trigger(
        user_id=str(user.id),
        platform="telegram",
        username=user.username,
        first_name=user.first_name,
        park_id="nn"
    )
    
    await _send_response(context, chat_id, response)
    return True


async def handle_birthday_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    message_text: str
) -> Optional[bool]:
    """
    Обработать текстовое сообщение в контексте birthday flow.
    
    Returns:
        True — обработано, False — передать AI, None — не birthday flow
    """
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    response = await birthday_flow_handler.handle_message(
        user_id=str(user.id),
        message=message_text,
        platform="telegram",
        username=user.username,
        first_name=user.first_name,
        park_id="nn"
    )
    
    if response is None:
        return None  # Не наш flow
    
    await _send_response(context, chat_id, response)
    return True


async def handle_birthday_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    callback_data: str
) -> Optional[bool]:
    """
    Обработать callback кнопки birthday flow.
    
    Returns:
        True если обработано, None если не наш callback
    """
    user = update.effective_user
    chat_id = update.callback_query.message.chat_id
    
    response = await birthday_flow_handler.handle_callback(
        user_id=str(user.id),
        callback_data=callback_data,
        platform="telegram",
        park_id="nn"
    )
    
    if response is None:
        return None  # Не наш callback
    
    await _send_response(context, chat_id, response, update)
    return True


def is_birthday_flow_active(user_id: str) -> bool:
    """Проверить, активен ли birthday flow для пользователя."""
    sm = get_state_machine(str(user_id), "telegram")
    state = sm.get_current_state()
    
    return state not in (BirthdayState.IDLE, BirthdayState.COMPLETE)


def check_birthday_trigger(message_text: str) -> bool:
    """Проверить, содержит ли сообщение триггер birthday."""
    return contains_birthday_trigger(message_text)


async def _send_response(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    response: FlowResponse,
    update: Update = None
):
    """Отправить FlowResponse в Telegram."""
    # Формируем кнопки
    keyboard = None
    if response.buttons:
        keyboard = []
        for btn in response.buttons:
            keyboard.append([
                InlineKeyboardButton(
                    text=btn.get("text", ""),
                    callback_data=btn.get("callback", "")
                )
            ])
        keyboard = InlineKeyboardMarkup(keyboard)
    
    # Удаляем старое сообщение с кнопками если это callback
    if update and update.callback_query:
        try:
            await update.callback_query.message.delete()
        except Exception:
            pass
    
    # Отправляем сообщение
    if response.image_path:
        with open(response.image_path, 'rb') as photo:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=response.text,
                parse_mode=response.parse_mode,
                reply_markup=keyboard
            )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=response.text,
            parse_mode=response.parse_mode,
            reply_markup=keyboard
        )


# Birthday callback patterns to check
BIRTHDAY_CALLBACKS = [
    "intent_birthday",
    "birthday_edit",
    "birthday_cancel",
    "birthday_contact",
    "phone_confirm_yes",
    "phone_confirm_no",
    "format_room",
    "format_zone",
    "time_1030",
    "time_1430",
    "time_1830",
    "extras_skip",
]


def is_birthday_callback(callback_data: str) -> bool:
    """Проверить, относится ли callback к birthday flow."""
    return callback_data in BIRTHDAY_CALLBACKS or callback_data.startswith("time_")
