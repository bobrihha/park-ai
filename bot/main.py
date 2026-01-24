"""Telegram & VK Bots — точка входа."""

import logging
import asyncio
from telegram import BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters

from bot.handlers import (
    start_command, handle_message, button_handler, error_handler,
    birthday_command, human_command, dynamic_command_handler, booking_command,
    hours_command, prices_command, discounts_command, events_command
)
from config.settings import TELEGRAM_BOT_TOKEN, VK_TOKEN, VK_GROUP_ID
from db import init_db, SessionLocal, BotCommand as DBBotCommand

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


async def post_init(application):
    """Инициализация после создания приложения."""
    # Устанавливаем меню команд из БД
    db = SessionLocal()
    try:
        db_commands = db.query(DBBotCommand).filter(DBBotCommand.is_active == True).order_by(DBBotCommand.order).all()
        
        commands = []
        if db_commands:
            for cmd in db_commands:
                commands.append(BotCommand(cmd.command, cmd.title))
        else:
            # Дефолтные команды для первого запуска
            commands = [
                BotCommand("start", "🏠 Главное меню"),
                BotCommand("booking", "📋 Моё бронирование"),
                BotCommand("prices", "💰 Цены"),
                BotCommand("birthday", "🎂 День рождения"),
                # ... остальные можно добавить или оставить пустым если БД пустая
            ]
            
        await application.bot.set_my_commands(commands)
        logger.info(f"Bot menu commands set successfully! ({len(commands)} commands)")
    except Exception as e:
        logger.error(f"Failed to set commands: {e}")
    finally:
        db.close()
    
    logger.info("Telegram bot initialized successfully!")


async def run_vk_bot_task():
    """Запустить VK бота (DISABLED)."""
    # Эта функция больше не используется, так как VK бот запускается отдельным сервисом
    pass


def main():
    """Запуск ботов."""
    try:
        import bot.handlers
        print(f"DEBUG: bot.handlers loaded from: {bot.handlers.__file__}", flush=True)
    except Exception as e:
        print(f"DEBUG: Failed to import bot.handlers: {e}", flush=True)

    # Проверяем токен Telegram
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "your_bot_token_here":
        logger.error("TELEGRAM_BOT_TOKEN not configured! Please set it in .env file")
        print("\n❌ Ошибка: Не настроен TELEGRAM_BOT_TOKEN!")
        print("1. Скопируйте .env.example в .env")
        print("2. Добавьте токен бота от @BotFather")
        print("3. Добавьте OpenAI API ключ")
        return
    
    # Инициализируем БД
    logger.info("Initializing database...")
    init_db()
    
    # Создаём Telegram приложение
    logger.info("Starting Telegram bot...")
    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    
    # Регистрируем обработчики команд
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("booking", booking_command))
    application.add_handler(CommandHandler("birthday", birthday_command))
    application.add_handler(CommandHandler("human", human_command))
    application.add_handler(CommandHandler("hours", hours_command))
    application.add_handler(CommandHandler("prices", prices_command))
    application.add_handler(CommandHandler("discounts", discounts_command))
    application.add_handler(CommandHandler("events", events_command))
    
    # Универсальный обработчик для остальных команд из БД
    application.add_handler(MessageHandler(filters.COMMAND, dynamic_command_handler))
    
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Обработчик ошибок
    application.add_error_handler(error_handler)
    
    # Запускаем оба бота параллельно
    logger.info("Bots are running! Press Ctrl+C to stop.")
    
    # Запускаем через asyncio.run для корректного управления event loop
    async def run_both():
        """Запуск обоих ботов параллельно."""
        async with application:
            await application.initialize()
            await application.start()
            
            # Запускаем VK бота параллельно как обычную async функцию
            # НЕ как background task
            async def telegram_polling():
                await application.updater.start_polling(drop_pending_updates=True)
                await asyncio.Event().wait()
            
            logger.info("Starting both bots concurrently...")
            
            # Запускаем оба бота через gather
            # VK бот запускается отдельным сервисом (jungle-vk), поэтому здесь только Телеграм
            await asyncio.gather(
                telegram_polling(),
                # run_vk_bot_task()  # DISABLED: Running via separate service
            )
    
    try:
        asyncio.run(run_both())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")


if __name__ == "__main__":
    main()
