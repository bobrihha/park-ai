"""
Telegram Adapter — пример интеграции MessageService с Telegram.

Этот модуль показывает как использовать MessageService в handlers.py.
Постепенно можно перенести логику из handlers.py сюда.
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from core.message_service import (
    MessageService, 
    UserInfo, 
    Platform, 
    MessageResult,
    get_message_service
)
from core.notifications import send_to_managers, send_to_birthday_channel

logger = logging.getLogger(__name__)


class TelegramAdapter:
    """
    Адаптер для использования MessageService в Telegram боте.
    
    Преобразует Telegram Update в UserInfo,
    вызывает MessageService и форматирует ответ.
    """
    
    def __init__(self, park_id: str = "nn"):
        self.service = get_message_service(park_id)
    
    async def handle_message(
        self, 
        update: Update, 
        context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """
        Обработать текстовое сообщение через MessageService.
        """
        user = update.effective_user
        message_text = update.message.text
        
        # Создаём UserInfo
        user_info = UserInfo(
            user_id=str(user.id),
            platform=Platform.TELEGRAM,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name
        )
        
        try:
            # Обрабатываем через MessageService
            result = await self.service.process_message(user_info, message_text)
            
            # Отправляем ответ
            await self._send_response(update, result)
            
            # Уведомляем менеджеров если нужно
            if result.should_notify_manager and result.manager_message:
                await self._notify_managers(result)
                
        except Exception as e:
            logger.error(f"Error handling message: {e}")
            await update.message.reply_text(
                "Упс, что-то пошло не так! 😅 Попробуйте ещё раз."
            )
    
    async def _send_response(self, update: Update, result: MessageResult) -> None:
        """Отправить ответ пользователю."""
        
        # Строим клавиатуру если есть кнопки
        reply_markup = None
        if result.buttons:
            keyboard = []
            for btn in result.buttons:
                keyboard.append([
                    InlineKeyboardButton(
                        text=btn.get("text", ""),
                        callback_data=btn.get("callback", "")
                    )
                ])
            reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Отправляем с картинкой или без
        if result.image_path:
            try:
                with open(result.image_path, 'rb') as photo:
                    await update.message.reply_photo(
                        photo=photo,
                        caption=result.text,
                        reply_markup=reply_markup,
                        parse_mode="HTML"
                    )
            except Exception as e:
                logger.error(f"Failed to send image: {e}")
                await update.message.reply_text(
                    result.text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
        else:
            await update.message.reply_text(
                result.text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
    
    async def _notify_managers(self, result: MessageResult) -> None:
        """Отправить уведомление менеджерам."""
        try:
            if result.manager_channel == "birthday":
                await send_to_birthday_channel(result.manager_message)
            else:
                await send_to_managers(result.manager_message)
        except Exception as e:
            logger.error(f"Failed to notify managers: {e}")


# Singleton
_adapter: TelegramAdapter = None

def get_telegram_adapter(park_id: str = "nn") -> TelegramAdapter:
    """Получить singleton адаптера."""
    global _adapter
    if _adapter is None:
        _adapter = TelegramAdapter(park_id)
    return _adapter


# ============ ПРИМЕР ИСПОЛЬЗОВАНИЯ ============
"""
В handlers.py можно использовать так:

from core.adapters.telegram_adapter import get_telegram_adapter

# В начале файла
adapter = get_telegram_adapter()

# В handle_message:
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Простые сообщения обрабатываем через MessageService
    await adapter.handle_message(update, context)

Постепенно можно перенести всю логику, оставив в handlers.py
только платформо-специфичные обработчики (команды, callback'и).
"""
