"""Telegram Bot — обработчики сообщений."""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import re
from datetime import datetime

from core import detect_intent, agent, rag, lead_collector
from db import SessionLocal, Session as DBSession, Message, Lead, BotCommand
from sqlalchemy.orm.attributes import flag_modified
from config.settings import MANAGER_CHAT_ID
from core.notifications import (
    send_to_managers, 
    send_to_birthday_channel,
    format_lead_message, 
    format_escalation_message, 
    needs_human_escalation,
    needs_lost_item_flow,
    format_lost_item_message,
    needs_booking_change_request,
    get_booking_change_type,
    format_booking_change_message,
    needs_photo_request,
    needs_photo_order,
    format_photo_request_message,
    format_photo_order_message,
    needs_partnership_proposal,
    format_partnership_message,
    needs_extras_request,
    get_extras_type,
    format_extras_request_message,
    needs_complaint_flow,
    format_complaint_message
)
from core.lead_service import (
    get_or_create_lead,
    update_lead_from_data,
    mark_lead_sent_to_manager,
    lead_to_dict,
    save_amocrm_deal_id,
    save_amocrm_contact_id,
    mark_status_notified,
    get_active_lead_info,
    force_create_new_lead,
    get_last_known_phone
)
from core.amocrm import send_lead_to_amocrm, amocrm_client
from core.messages import BIRTHDAY_WELCOME_MESSAGE

logger = logging.getLogger(__name__)

# Картинки для основных разделов (локальные файлы на сервере)
import os
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES_DIR = os.path.join(BASE_DIR, "static", "images")

IMAGES = {
    "general": os.path.join(IMAGES_DIR, "park.jpg"),           # О парке
    "birthday": os.path.join(IMAGES_DIR, "birthday.jpg"),      # День рождения
    "events": os.path.join(IMAGES_DIR, "events.jpg"),          # Афиша
    "confirmation": os.path.join(IMAGES_DIR, "confirmation.png"),  # Подтверждение заявки
}


async def send_photo_or_text(bot, chat_id: int, image_key: str, caption: str, parse_mode: str = "HTML"):
    """
    Отправить фото с текстом. Если фото нет — отправить только текст.
    """
    image_path = IMAGES.get(image_key)
    if image_path and os.path.exists(image_path):
        try:
            with open(image_path, 'rb') as photo_file:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_file,
                    caption=caption,
                    parse_mode=parse_mode
                )
                return
        except Exception as e:
            logger.warning(f"Failed to send image {image_key}: {e}")
    
    # Fallback: только текст
    await bot.send_message(
        chat_id=chat_id,
        text=caption,
        parse_mode=parse_mode
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start."""
    user = update.effective_user
    
    # Создаём или получаем сессию в БД
    db = SessionLocal()
    try:
        session = db.query(DBSession).filter(DBSession.telegram_id == str(user.id)).first()
        if not session:
            session = DBSession(telegram_id=str(user.id), park_id="nn")
            db.add(session)
            db.commit()
        else:
            # Сбрасываем intent для нового диалога
            session.intent = "unknown"
            session.lead_data = {}
            db.commit()
    finally:
        db.close()
    
    # Приветственное сообщение с кнопками
    keyboard = [
        [InlineKeyboardButton("📋 Моё бронирование", callback_data="my_booking")],
        [InlineKeyboardButton("🎫 Узнать о парке", callback_data="intent_general")],
        [InlineKeyboardButton("🎉 Организовать праздник", callback_data="intent_birthday")],
        [InlineKeyboardButton("🎪 Афиша и события", callback_data="intent_events")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"Добро пожаловать в Джунгли Сити, {user.first_name}! 💚💜\n\n"
        "Здесь каждый день — приключение, а ваш ребёнок — главный герой джунглей!\n\n"
        "Я Джуси — ваш проводник по парку. С радостью помогу:\n"
        "• Узнать всё о парке и ценах\n"
        "• Организовать незабываемый день рождения\n"
        "• Рассказать о ближайших событиях\n\n"
        "Что вас интересует? 👇",
        reply_markup=reply_markup
    )


from core.utils import (
    get_prices_from_knowledge,
    get_afisha_events,
    parse_user_date,
    format_date_ru,
    build_birthday_date_question,
    parse_kids_count,
    filter_extras_from_message,
    should_defer_phone_request,
    extract_phone_from_message,
    extract_format_from_message,
    build_format_choice_message,
)


async def prices_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /prices — цены на билеты."""
    prices = get_prices_from_knowledge()
    
    await update.message.reply_text(
        "💰 <b>Цены на безлимитный билет</b>\n\n"
        f"🟢 Понедельник (супер-цена): <b>{prices['monday']} ₽</b>\n"
        f"🔵 Будни (вт-пт): <b>{prices['weekday']} ₽</b>\n"
        f"🔴 Выходные: <b>{prices['weekend']} ₽</b>\n\n"
        "✅ Взрослые — БЕСПЛАТНО\n"
        "✅ Дети до 1 года — БЕСПЛАТНО\n\n"
        "<b>Скидки:</b>\n"
        "• Дети 1-4 года: -20% в будни\n"
        "• Многодетные: -30% (вт-вс)\n"
        "• После 20:00: -50%\n"
        "• Именинник: -50% (±5 дней от ДР)\n\n"
        "Напишите, если нужна помощь с расчётом! 😊",
        parse_mode="HTML"
    )


async def birthday_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /birthday — запуск бронирования ДР."""
    user = update.effective_user
    db = SessionLocal()
    try:
        # 1. Проверяем, есть ли АКТИВНАЯ сделка в CRM (amocrm_deal_id != None)
        existing_deal = db.query(Lead).filter(
            Lead.telegram_id == str(user.id),
            Lead.park_id == "nn",
            Lead.amocrm_deal_id != None,
            Lead.status.in_(["new", "contacted", "booked"])
        ).order_by(Lead.created_at.desc()).first()

        if existing_deal and existing_deal.event_date:
            # Есть активная сделка — спрашиваем: изменить или дополнительное?
            keyboard = [
                [InlineKeyboardButton("✏️ Изменить текущее", callback_data="booking_edit_existing")],
                [InlineKeyboardButton("➕ Создать дополнительное", callback_data="booking_additional")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"🎉 <b>У вас уже есть бронирование!</b>\n\n"
                f"📅 Дата: <b>{existing_deal.event_date}</b>\n"
                f"👶 Детей: {existing_deal.kids_count or 'не указано'}\n\n"
                f"Хотите изменить это бронирование или создать дополнительное?",
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
            # Сохраняем ID существующего лида для последующего редактирования
            context.user_data["existing_lead_id"] = existing_deal.id
            return
        
        # 2. Проверяем черновик (незавершённая заявка без CRM)
        draft_lead = db.query(Lead).filter(
            Lead.telegram_id == str(user.id),
            Lead.park_id == "nn",
            Lead.status.in_(["new", "contacted"]),
            Lead.sent_to_manager == False,
            Lead.amocrm_deal_id == None
        ).first()

        if draft_lead:
            # Есть черновик — спрашиваем продолжить или начать заново
            keyboard = [
                [InlineKeyboardButton("✏️ Продолжить текущую", callback_data="lead_continue")],
                [InlineKeyboardButton("🆕 Начать заново", callback_data="lead_new")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"🎉 <b>У вас есть незавершенная заявка!</b>\n\n"
                f"Хотите продолжить её заполнение или начать заново?",
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
            return

        # 3. Нет ни сделки, ни черновика — стандартный флоу
        session = db.query(DBSession).filter(DBSession.telegram_id == str(user.id)).first()
        if not session:
            session = DBSession(telegram_id=str(user.id), park_id="nn")
            db.add(session)
        session.intent = "birthday"
        session.lead_data = {}
        db.commit()
    finally:
        db.close()
    
    # Стандартное приветствие для НОВОЙ заявки (единое с web/VK)
    await update.message.reply_text(
        BIRTHDAY_WELCOME_MESSAGE,
        parse_mode="HTML"
    )


async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /schedule — режим работы."""
    await update.message.reply_text(
        "🕐 <b>Режим работы Джунгли Сити</b>\n\n"
        "📍 Нижний Новгород, ТЦ «Лента»\n\n"
        "• Понедельник: 12:00 - 22:00\n"
        "• Вторник - Воскресенье: 10:00 - 22:00\n\n"
        "⚠️ Вход в парк до 21:00\n"
        "🍕 Ресторан принимает заказы до 21:00",
        parse_mode="HTML"
    )


async def afisha_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /afisha — афиша событий."""
    await update.message.reply_text(
        "🎪 <b>Афиша Джунгли Сити</b>\n\n"
        "Актуальные события и мероприятия:\n"
        "👉 <a href='https://nn.jucity.ru/afisha/'>Открыть афишу</a>\n\n"
        "У нас регулярно проходят:\n"
        "• Шоу-программы\n"
        "• Мастер-классы\n"
        "• Дискотеки\n"
        "• Праздничные мероприятия\n\n"
        "Спрашивайте — расскажу подробнее! 🌟",
        parse_mode="HTML"
    )


async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /rules — правила парка."""
    await update.message.reply_text(
        "📋 <b>Правила посещения Джунгли Сити</b>\n\n"
        "🧦 <b>Носки обязательны</b> на игровой территории\n\n"
        "👨‍👩‍👧 <b>Дети под присмотром</b> взрослых\n\n"
        "🍕 <b>Своя еда запрещена</b>\n"
        "   (кроме детского питания и воды)\n\n"
        "🚫 <b>Запрещено:</b>\n"
        "• Алкоголь\n"
        "• Домашние животные\n"
        "• Опасные предметы\n\n"
        "♿ Есть пандусы и лифты через ТЦ «Лента»",
        parse_mode="HTML"
    )


async def human_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /human — вызов живого менеджера."""
    user = update.effective_user
    
    # Отправляем уведомление менеджерам
    escalation_msg = format_escalation_message(
        platform="telegram",
        user_id=str(user.id),
        username=user.username,
        user_name=user.first_name or "Неизвестный",
        message="[Запрос через команду /human]"
    )
    await send_to_managers(escalation_msg)
    
    await update.message.reply_text(
        "👤 <b>Запрос передан менеджеру!</b>\n\n"
        "Наш специалист скоро свяжется с вами.\n\n"
        "📞 Или позвоните: <b>+7 (831) 213-50-50</b>\n"
        "💬 WhatsApp: +7 (962) 509-74-93",
        parse_mode="HTML"
    )


async def contacts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /contacts — контакты и как добраться."""
    await update.message.reply_text(
        "📍 <b>Как нас найти</b>\n\n"
        "<b>Адрес:</b>\n"
        "г. Нижний Новгород, ул. Коминтерна, д. 11\n"
        "ТЦ «Лента», 1 этаж\n\n"
        "<b>Телефоны:</b>\n"
        "📞 +7 (831) 213-50-50\n"
        "💬 WhatsApp: +7 (962) 509-74-93\n\n"
        "<b>Как добраться:</b>\n"
        "🚇 Метро «Буревестник» — 250 м\n"
        "🚌 Автобус 90, 95, 71, 78, 29 → ост. «Варя»\n"
        "🚋 Троллейбус 5, 8 → ост. «Варя»\n"
        "🚗 Бесплатная парковка у ТЦ",
        parse_mode="HTML"
    )


async def cafe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /cafe — меню ресторана."""
    await update.message.reply_text(
        "🍕 <b>Ресторан Джунгли Сити</b>\n\n"
        "У нас вкусно и для детей, и для взрослых!\n\n"
        "📖 <b>Меню:</b>\n"
        "👉 <a href='https://catalog.botcicada.ru/menu.html'>Открыть меню</a>\n\n"
        "🎂 <b>Торты на заказ:</b>\n"
        "👉 <a href='https://catalog.botcicada.ru/cakes.html'>Каталог тортов</a>\n\n"
        "⏰ Ресторан работает до 21:00",
        parse_mode="HTML"
    )


async def promo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /promo — текущие акции."""
    await update.message.reply_text(
        "🎁 <b>Акции Джунгли Сити</b>\n\n"
        "На данный момент специальных акций нет.\n\n"
        "Следите за обновлениями на нашем сайте:\n"
        "👉 https://nn.jucity.ru/\n\n"
        "А пока — приглашаем отметить праздник у нас! 🎉\n"
        "Напишите /birthday для расчёта стоимости.",
        parse_mode="HTML"
    )


async def hours_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /hours — часы работы."""
    await update.message.reply_text(
        "🕐 <b>Часы работы Джунгли Сити</b>\n\n"
        "📅 <b>Ежедневно:</b>\n"
        "⏰ 10:00 — 21:00\n\n"
        "🎢 Аттракционы работают до 20:30\n"
        "🍕 Ресторан до 21:00\n\n"
        "📍 ТЦ «Лента», ул. Коминтерна, 11\n"
        "📞 +7 (831) 213-50-50",
        parse_mode="HTML"
    )


async def prices_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /prices — цены на билеты."""
    prices = get_prices_from_knowledge()
    await update.message.reply_text(
        "💰 <b>Цены на билеты Джунгли Сити</b>\n\n"
        f"📅 <b>Понедельник:</b> {prices['monday']} ₽\n"
        f"📅 <b>Будни (вт-пт):</b> {prices['weekday']} ₽\n"
        f"📅 <b>Выходные и праздники:</b> {prices['weekend']} ₽\n\n"
        "✅ Безлимит на все аттракционы весь день!\n"
        "👶 Дети до 1 года — бесплатно\n"
        "👨 Взрослые без билета — бесплатно\n\n"
        "🎂 День рождения? Напишите /birthday — именинник бесплатно!",
        parse_mode="HTML"
    )


async def discounts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /discounts — скидки."""
    await update.message.reply_text(
        "🎁 <b>Скидки и акции Джунгли Сити</b>\n\n"
        "🎂 <b>День рождения:</b>\n"
        "• Именинник бесплатно (при 7+ детях)\n"
        "• Комната на 3 часа — бесплатно\n\n"
        "👨‍👩‍👧‍👦 <b>Многодетные семьи:</b>\n"
        "• Скидка 10% при предъявлении документов\n\n"
        "📅 <b>День недели:</b>\n"
        "• Понедельник — самые низкие цены!\n\n"
        "💳 <b>Бонусная программа:</b>\n"
        "• Накапливайте баллы за посещения\n"
        "• Оплачивайте до 50% баллами\n\n"
        "Подробности: /birthday или /human",
        parse_mode="HTML"
    )


async def events_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /events — афиша мероприятий."""
    await update.message.reply_text(
        "🎉 <b>Афиша Джунгли Сити</b>\n\n"
        "Актуальные мероприятия и праздники смотрите:\n\n"
        "📱 <b>Телеграм-канал:</b>\n"
        "👉 https://t.me/juicitynn\n\n"
        "📸 <b>Instagram:</b>\n"
        "👉 @juicitynn\n\n"
        "🌐 <b>Сайт:</b>\n"
        "👉 https://nn.jucity.ru/\n\n"
        "Подпишитесь, чтобы не пропустить! 💚",
        parse_mode="HTML"
    )


def format_booking_info(lead) -> str:
    """Форматировать информацию о бронировании для пользователя."""
    status_emoji = {
        "new": "📝 Новая заявка",
        "contacted": "📞 Связываемся",
        "booked": "✅ Подтверждено"
    }
    
    text = f"📋 <b>Ваше бронирование #{lead.id}</b>\n\n"
    text += f"📊 Статус: {status_emoji.get(lead.status, lead.status)}\n"
    
    if lead.event_date:
        text += f"📅 Дата: <b>{lead.event_date}</b>\n"
    if lead.time:
        text += f"⏰ Время: <b>{lead.time}</b>\n"
    if lead.kids_count:
        text += f"👶 Детей: {lead.kids_count}\n"
    if lead.adults_count:
        text += f"👨 Взрослых: {lead.adults_count}\n"
    if lead.child_name:
        text += f"🎂 Именинник: {lead.child_name}"
        if lead.child_age:
            text += f" ({lead.child_age} лет)"
        text += "\n"
    if lead.format:
        text += f"🏠 Формат: {lead.format}\n"
    if lead.room:
        text += f"🚪 Комната: {lead.room}\n"
    if lead.customer_name:
        text += f"👤 Контакт: {lead.customer_name}\n"
    if lead.phone:
        text += f"📞 Телефон: {lead.phone}\n"
    
    return text


async def booking_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /booking — показать информацию о бронировании."""
    user = update.effective_user
    
    db = SessionLocal()
    try:
        # Ищем активные лиды пользователя (только отправленные менеджеру)
        leads = db.query(Lead).filter(
            Lead.telegram_id == str(user.id),
            Lead.status.in_(["new", "contacted", "booked"]),
            Lead.sent_to_manager == True
        ).order_by(Lead.created_at.desc()).limit(3).all()
        
        if not leads:
            # Проверяем черновики
            drafts = db.query(Lead).filter(
                Lead.telegram_id == str(user.id),
                Lead.sent_to_manager == False,
                Lead.status.in_(["new", "contacted"])
            ).first()
            
            if drafts:
                await update.message.reply_text(
                    "📝 У вас есть незавершённая заявка на праздник.\n\n"
                    "Чтобы продолжить оформление, напишите /birthday\n"
                    "или задайте мне любой вопрос! 😊"
                )
            else:
                keyboard = [
                    [InlineKeyboardButton("🎉 Забронировать праздник", callback_data="intent_birthday")]
                ]
                await update.message.reply_text(
                    "📋 У вас пока нет активных бронирований.\n\n"
                    "Хотите организовать незабываемый день рождения? 🎂",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            return
        
        # Показываем бронирования
        for lead in leads:
            text = format_booking_info(lead)
            
            keyboard = [
                [InlineKeyboardButton("✏️ Изменить дату/время", callback_data=f"change_{lead.id}_datetime")],
                [InlineKeyboardButton("👥 Изменить кол-во гостей", callback_data=f"change_{lead.id}_guests")],
                [InlineKeyboardButton("🎁 Добавить услуги", callback_data=f"change_{lead.id}_extras")],
                [InlineKeyboardButton("❌ Отменить бронь", callback_data=f"change_{lead.id}_cancel")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
    finally:
        db.close()


async def dynamic_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Универсальный обработчик динамических команд из БД."""
    command_name = update.message.text.replace("/", "").split("@")[0]  # удаляем @botname если есть
    
    db = SessionLocal()
    try:
        command = db.query(BotCommand).filter(
            BotCommand.command == command_name, 
            BotCommand.is_active == True
        ).first()
        
        if command and command.response:
            await update.message.reply_text(
                command.response,
                parse_mode="HTML"
            )
        else:
            # Если команда не найдена в БД или неактивна
            # Можно отправить заглушку или просто игнорировать
            logger.warning(f"Command /{command_name} not found or inactive.")
    except Exception as e:
        logger.error(f"Error executing dynamic command /{command_name}: {e}")
    finally:
        db.close()


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий на инлайн-кнопки."""
    query = update.callback_query
    await query.answer()
    
    # Получаем сессию
    db = SessionLocal()
    session = db.query(DBSession).filter(DBSession.telegram_id == str(query.from_user.id)).first()
    chat_id = query.message.chat_id
    
    try:
        if query.data == "lead_continue":
            # Пользователь решил продолжить текущую заявку
            if session:
                session.intent = "birthday"
                # lead_data НЕ сбрасываем, чтобы бот знал контекст
                
                # Загружаем данные из существующего лида
                active_lead = db.query(Lead).filter(
                    Lead.telegram_id == str(query.from_user.id),
                    Lead.park_id == "nn",
                    Lead.status.in_(["new", "contacted"]),
                    Lead.sent_to_manager == False
                ).first()
                if active_lead:
                    session.lead_data = lead_to_dict(active_lead)
                
                db.commit()

            try:
                await query.message.delete()
            except Exception:
                pass
            
            await update.callback_query.message.reply_text(
                "Отлично! Продолжаем оформление. На чем мы остановились? 😊"
            )

        elif query.data == "lead_new":
            # Пользователь хочет новую заявку. Старую помечаем как "deferred" (отложенную)
            active_lead = db.query(Lead).filter(
                Lead.telegram_id == str(query.from_user.id),
                Lead.park_id == "nn",
                Lead.status.in_(["new", "contacted"]),
                Lead.sent_to_manager == False
            ).first()
            
            if active_lead:
                active_lead.status = "deferred"
                db.commit()
            
            if session:
                session.intent = "birthday"
                session.lead_data = {}  # Сбрасываем для новой
                db.commit()

            try:
                await query.message.delete()
            except Exception:
                pass
            
            # Запускаем стандартный флоу новой заявки (картинка + текст)
            prices = get_prices_from_knowledge()
            caption = (
                "🎉 <b>День рождения в Джунгли Сити!</b>\n\n"
                "Что входит (от 6 детей):\n"
                "✅ Комната на 3 часа — БЕСПЛАТНО\n"
                "✅ Именинник — БЕСПЛАТНО (только при 7+ детях!)\n"
                "✅ Взрослые — БЕСПЛАТНО\n"
                "✅ Безлимит на все аттракционы весь день\n\n"
                f"<b>Цены на билеты:</b>\n"
                f"• Будни (вт-пт): {prices['weekday']} ₽\n"
                f"• Выходные: {prices['weekend']} ₽\n"
                f"• Понедельник: {prices['monday']} ₽\n\n"
                "ℹ️ Если детей меньше 7 — можно забронировать столик в ресторане (именинник со скидкой 50% на вход)\n\n"
                "Чтобы рассчитать и забронировать — ответьте:\n"
                "📅 <b>На какую дату планируете праздник?</b>"
            )
            await send_photo_or_text(context.bot, chat_id, "birthday", caption, "HTML")

        elif query.data == "intent_birthday":
            if session:
                session.intent = "birthday"
                db.commit()
            
            # Удаляем старое сообщение с кнопками
            try:
                await query.message.delete()
            except Exception:
                pass
            
            # СНАЧАЛА проверяем есть ли АКТИВНАЯ сделка в CRM
            existing_deal = db.query(Lead).filter(
                Lead.telegram_id == str(query.from_user.id),
                Lead.park_id == "nn",
                Lead.amocrm_deal_id != None,
                Lead.status.in_(["new", "contacted", "booked"])
            ).order_by(Lead.created_at.desc()).first()

            if existing_deal and existing_deal.event_date:
                # Есть активная сделка — спрашиваем: изменить или дополнительное?
                keyboard = [
                    [InlineKeyboardButton("✏️ Изменить текущее", callback_data="booking_edit_existing")],
                    [InlineKeyboardButton("➕ Создать дополнительное", callback_data="booking_additional")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🎉 <b>У вас уже есть бронирование!</b>\n\n"
                         f"📅 Дата: <b>{existing_deal.event_date}</b>\n"
                         f"👶 Детей: {existing_deal.kids_count or 'не указано'}\n\n"
                         f"Хотите изменить это бронирование или создать дополнительное?",
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
                context.user_data["existing_lead_id"] = existing_deal.id
                return
            
            # Проверяем есть ли клиент в AmoCRM (возвратный клиент БЕЗ активной сделки)
            contact = await amocrm_client.find_contact_by_telegram_id(query.from_user.id)
            found_phone = None
            found_name = None
            
            if contact:
                contact_info = amocrm_client.get_contact_info(contact)
                found_phone = contact_info.get("phone")
                found_name = contact_info.get("name") or query.from_user.first_name
                
                if found_phone:
                    # Создаём лид и сохраняем ТОЛЬКО имя (телефон после подтверждения)
                    # removed redundant import
                    current_lead = get_or_create_lead(query.from_user.id, park_id="nn", username=query.from_user.username)
                    update_lead_from_data(current_lead.id, {
                        "customer_name": found_name
                    })
                    
                    # Сохраняем телефон и lead_id в context для подтверждения ПОСЛЕ количества детей
                    context.user_data["pending_phone_confirm"] = found_phone
                    context.user_data["pending_lead_id"] = current_lead.id
                    context.user_data["pending_customer_name"] = found_name
                    
                    logger.info(f"Found returning customer: {found_name}, phone={found_phone}, will confirm after kids count")
            
            # Если контакт не найден или нет телефона — стандартный флоу
            # Отправляем фото с текстом
            caption = BIRTHDAY_WELCOME_MESSAGE
            await send_photo_or_text(context.bot, chat_id, "birthday", caption)
        
        # Обработка выбора: изменить текущую заявку или создать новую
        elif query.data == "booking_modify":
            # Пользователь хочет изменить текущую заявку
            try:
                await query.message.delete()
            except Exception:
                pass
            
            pending_date = context.user_data.get("pending_new_date", "")
            
            # Извлекаем дату из сообщения
            import re
            date_pattern = r'\b(\d{1,2})\s*(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\b'
            match = re.search(date_pattern, pending_date.lower())
            if match:
                extracted_date = f"{match.group(1)} {match.group(2)}"
                # Обновляем дату в текущем лиде
                active_info = get_active_lead_info(update.effective_user.id)
                if active_info:
                    # removed redundant import
                    update_lead_from_data(active_info["lead_id"], {"event_date": extracted_date})
                    logger.info(f"Updated Lead #{active_info['lead_id']} with new date: {extracted_date}")
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ Отлично, изменила дату в вашей заявке!\n\n📅 Новая дата: {pending_date}\n\nЕсли нужно что-то ещё изменить — просто напишите! 😊"
            )
            # Очищаем pending
            context.user_data.pop("pending_new_date", None)
            
        elif query.data == "booking_new":
            # Пользователь хочет создать новое бронирование
            try:
                await query.message.delete()
            except Exception:
                pass
            
            pending_date = context.user_data.get("pending_new_date", "")
            
            # Извлекаем дату
            import re
            date_pattern = r'\b(\d{1,2})\s*(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\b'
            match = re.search(date_pattern, pending_date.lower())
            extracted_date = f"{match.group(1)} {match.group(2)}" if match else pending_date
            
            # Получаем данные из старой заявки (имя) и телефон из ЛЮБОЙ заявки
            old_lead_info = get_active_lead_info(update.effective_user.id)
            old_name = old_lead_info.get("customer_name") if old_lead_info else None
            
            # Ищем последний известный телефон (может быть в любой заявке)
            old_phone = get_last_known_phone(update.effective_user.id)
            logger.info(f"Creating new booking: name={old_name}, last known phone={old_phone}")
            
            # Создаём новую заявку с датой
            new_lead = force_create_new_lead(
                update.effective_user.id,
                park_id="nn",
                username=update.effective_user.username,
                source="telegram"
            )
            
            # Сохраняем дату и имя (телефон НЕ сохраняем — ждём подтверждения!)
            # removed redundant import
            update_data = {"event_date": extracted_date}
            if old_name:
                update_data["customer_name"] = old_name
            update_lead_from_data(new_lead.id, update_data)
            logger.info(f"Created new Lead #{new_lead.id} with date: {extracted_date}")
            
            # Если есть старый телефон — запоминаем для подтверждения
            if old_phone:
                context.user_data["pending_phone_confirm"] = old_phone
                context.user_data["pending_lead_id"] = new_lead.id
                logger.info(f"Set pending_phone_confirm: {old_phone} for lead {new_lead.id}")
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✨ Отлично, начинаем новое бронирование!\n\n📅 Дата: {extracted_date}\n\n👶 Сколько детей будет на празднике, включая именинника?"
            )
            # Очищаем pending date
            context.user_data.pop("pending_new_date", None)
        
        # ============ НОВЫЕ ОБРАБОТЧИКИ: Изменить/Дополнительное ============
        
        elif query.data == "booking_edit_existing":
            # Пользователь хочет изменить существующее бронирование
            try:
                await query.message.delete()
            except Exception:
                pass
            
            existing_lead_id = context.user_data.get("existing_lead_id")
            if existing_lead_id:
                # Загружаем данные существующего лида
                lead = db.query(Lead).filter(Lead.id == existing_lead_id).first()
                if lead:
                    # Проверяем статус сделки в AmoCRM
                    deal_in_work = False
                    if lead.amocrm_deal_id:
                        try:
                            deal_in_work = await amocrm_client.is_deal_in_work(int(lead.amocrm_deal_id))
                        except Exception as e:
                            logger.error(f"Failed to check deal status: {e}")
                    
                    if deal_in_work:
                        # Сделка взята в работу — изменения через менеджера
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text="📋 Ваше бронирование уже взято в работу феями праздников! 🧚‍♀️\n\n"
                                 "Напишите, что хотите изменить, и я передам вашу просьбу менеджеру.\n"
                                 "Например: «хочу перенести на другую дату» или «добавить аниматора»."
                        )
                    else:
                        # Сделка ещё новая — можно редактировать напрямую
                        if session:
                            session.intent = "birthday"
                            session.lead_data = lead_to_dict(lead)
                            db.commit()
                        
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"✏️ Редактируем бронирование на {lead.event_date}\n\n"
                                 f"Текущие данные:\n"
                                 f"📅 Дата: {lead.event_date}\n"
                                 f"👶 Детей: {lead.kids_count or 'не указано'}\n"
                                 f"🎪 Формат: {lead.format or 'не выбран'}\n\n"
                                 "Что хотите изменить? Просто напишите новые данные."
                        )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="Не удалось найти бронирование. Попробуйте /birthday заново."
                )
        
        elif query.data == "booking_additional":
            # Пользователь хочет создать ДОПОЛНИТЕЛЬНОЕ бронирование
            try:
                await query.message.delete()
            except Exception:
                pass
            
            # Устанавливаем специальный intent для короткого флоу
            if session:
                session.intent = "birthday_additional"
                session.lead_data = {}
                db.commit()
            
            # Получаем телефон из существующего контакта (не будем спрашивать заново)
            contact = await amocrm_client.find_contact_by_telegram_id(query.from_user.id)
            if contact:
                contact_info = amocrm_client.get_contact_info(contact)
                phone = contact_info.get("phone")
                name = contact_info.get("name") or query.from_user.first_name
                context.user_data["additional_booking_phone"] = phone
                context.user_data["additional_booking_name"] = name
                logger.info(f"Additional booking: using existing contact phone={phone}, name={name}")
            
            await context.bot.send_message(
                chat_id=chat_id,
                text="➕ Создаём дополнительное бронирование!\n\n"
                     "📅 На какую дату планируете этот праздник?"
            )
        
        # ============ КОНЕЦ НОВЫХ ОБРАБОТЧИКОВ ============
        
        # Подтверждение телефона для нового бронирования
        elif query.data == "confirm_phone_yes":
            try:
                await query.message.delete()
            except Exception:
                pass
            
            pending_phone = context.user_data.get("pending_phone_confirm")
            pending_lead_id = context.user_data.get("pending_lead_id")
            
            if pending_phone and pending_lead_id:
                # Сохраняем телефон в лид
                # removed redundant import
                update_lead_from_data(pending_lead_id, {"phone": pending_phone})
                logger.info(f"Lead #{pending_lead_id} confirmed phone: {pending_phone}")
                
                # Отправляем в AmoCRM
                lead_data = lead_to_dict(get_or_create_lead(update.effective_user.id))
                if lead_data.get("phone"):
                    result = await send_lead_to_amocrm(
                        lead_data, 
                        telegram_id=update.effective_user.id,
                        username=update.effective_user.username
                    )
                    if result and result[0]:
                        deal_id, contact_id = result
                        save_amocrm_deal_id(pending_lead_id, deal_id)
                        if contact_id:
                            save_amocrm_contact_id(pending_lead_id, contact_id)
                        logger.info(f"Lead #{pending_lead_id} sent to AmoCRM, deal_id: {deal_id}")
                        # Добавляем историю переписки в AmoCRM
                        try:
                            db_local = SessionLocal()
                            session = db_local.query(DBSession).filter(DBSession.telegram_id == str(update.effective_user.id)).first()
                            if session:
                                msgs = db_local.query(Message).filter(Message.session_id == session.id).order_by(Message.id).limit(30).all()
                                chat_history_text = "\n".join(
                                    [f"{'Клиент' if m.role == 'user' else 'Бот'}: {m.content}" for m in msgs]
                                )
                                await amocrm_client.add_note(int(deal_id), f"📱 История переписки:\n\n{chat_history_text}")
                            db_local.close()
                        except Exception as ne:
                            logger.error(f"Failed to add chat history note: {ne}")
            
            # Очищаем pending
            context.user_data.pop("pending_phone_confirm", None)
            context.user_data.pop("pending_lead_id", None)
            context.user_data.pop("pending_customer_name", None)

            # Уведомляем менеджеров о заявке
            try:
                msg_text = format_lead_message("telegram", str(update.effective_user.id), lead_to_dict(get_or_create_lead(update.effective_user.id)))
                await send_to_birthday_channel(msg_text)
                mark_lead_sent_to_manager(pending_lead_id)
            except Exception as e:
                logger.error(f"Failed to notify managers after phone confirm: {e}")

            # Переходим к следующему шагу (короткие вопросы)
            lead_after = lead_to_dict(get_or_create_lead(update.effective_user.id))
            format_msg = build_format_choice_message(lead_after.get("event_date"), lead_after.get("kids_count"))
            await context.bot.send_message(
                chat_id=chat_id,
                text=format_msg or "🎉 Какой формат праздника предпочитаете — тематическая комната или столик в ресторане?"
            )
        
        elif query.data == "confirm_phone_no":
            try:
                await query.message.delete()
            except Exception:
                pass
            
            # Очищаем pending телефон
            context.user_data.pop("pending_phone_confirm", None)
            
            await context.bot.send_message(
                chat_id=chat_id,
                text="📱 Хорошо! Напишите, пожалуйста, ваш номер телефона для связи."
            )
        
        # Подтверждение телефона для ВОЗВРАТНОГО клиента (найден в AmoCRM)
        elif query.data == "confirm_returning_phone_yes":
            try:
                await query.message.delete()
            except Exception:
                pass
            
            pending_phone = context.user_data.get("pending_phone_confirm")
            pending_lead_id = context.user_data.get("pending_lead_id")
            pending_name = context.user_data.get("pending_customer_name")
            
            if pending_phone and pending_lead_id:
                # Сохраняем телефон в лид
                # removed redundant import
                update_lead_from_data(pending_lead_id, {"phone": pending_phone})
                logger.info(f"Lead #{pending_lead_id} confirmed returning phone: {pending_phone}")
                
                # Отправляем в AmoCRM
                lead_data_for_crm = lead_to_dict(get_or_create_lead(update.effective_user.id))
                if lead_data_for_crm.get("phone"):
                    result = await send_lead_to_amocrm(
                        lead_data_for_crm, 
                        telegram_id=update.effective_user.id,
                        username=update.effective_user.username
                    )
                    if result and result[0]:
                        deal_id, contact_id = result
                        save_amocrm_deal_id(pending_lead_id, deal_id)
                        if contact_id:
                            save_amocrm_contact_id(pending_lead_id, contact_id)
                        logger.info(f"Lead #{pending_lead_id} sent to AmoCRM, deal_id: {deal_id}")
                        # Добавляем историю переписки в AmoCRM
                        try:
                            db_local = SessionLocal()
                            session = db_local.query(DBSession).filter(DBSession.telegram_id == str(update.effective_user.id)).first()
                            if session:
                                msgs = db_local.query(Message).filter(Message.session_id == session.id).order_by(Message.id).limit(30).all()
                                chat_history_text = "\n".join(
                                    [f"{'Клиент' if m.role == 'user' else 'Бот'}: {m.content}" for m in msgs]
                                )
                                await amocrm_client.add_note(int(deal_id), f"📱 История переписки:\n\n{chat_history_text}")
                            db_local.close()
                        except Exception as ne:
                            logger.error(f"Failed to add chat history note: {ne}")
            
            # Очищаем pending
            context.user_data.pop("pending_phone_confirm", None)
            context.user_data.pop("pending_lead_id", None)
            context.user_data.pop("pending_customer_name", None)
            
            # Уведомляем менеджеров о заявке
            try:
                msg_text = format_lead_message("telegram", str(update.effective_user.id), lead_to_dict(get_or_create_lead(update.effective_user.id)))
                await send_to_birthday_channel(msg_text)
                mark_lead_sent_to_manager(pending_lead_id)
            except Exception as e:
                logger.error(f"Failed to notify managers after returning phone confirm: {e}")

            # Дальше ведём по короткому сценарию
            lead = get_or_create_lead(update.effective_user.id)
            lead_data = lead_to_dict(lead)
            if not lead_data.get("event_date"):
                # Если даты ещё нет — начинаем с приветствия
                caption = BIRTHDAY_WELCOME_MESSAGE
                await send_photo_or_text(context.bot, chat_id, "birthday", caption)
            elif not lead_data.get("kids_count"):
                date_obj = parse_user_date(lead_data["event_date"])
                if date_obj:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=build_birthday_date_question(date_obj)
                    )
                else:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="👶 Сколько детей будет всего, включая именинника?"
                    )
            else:
                format_msg = build_format_choice_message(lead_data.get("event_date"), lead_data.get("kids_count"))
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=format_msg or "🎉 Какой формат праздника предпочитаете — тематическая комната или столик в ресторане?"
                )
        
        elif query.data == "confirm_returning_phone_no":
            try:
                await query.message.delete()
            except Exception:
                pass
            
            # Очищаем pending телефон (оставляем lead_id для сохранения нового номера)
            context.user_data.pop("pending_phone_confirm", None)
            context.user_data["waiting_for_new_phone"] = True
            
            await context.bot.send_message(
                chat_id=chat_id,
                text="📱 Хорошо! Напишите, пожалуйста, ваш актуальный номер телефона для связи."
            )
        
        # ============ ОБРАБОТКА ВЫБОРА ФОРМАТА (format_room / format_zone) ============
        elif query.data == "format_room":
            try:
                await query.message.delete()
            except Exception:
                pass
            
            # Сохраняем выбранный формат
            lead = get_or_create_lead(query.from_user.id)
            update_lead_from_data(lead.id, {"format": "комната"})
            
            # Обновляем сессию
            if session:
                ld = session.lead_data or {}
                ld["step"] = "time"
                session.lead_data = ld
                flag_modified(session, "lead_data")
                db.commit()
            
            # Показываем слоты времени
            keyboard = [
                [InlineKeyboardButton("🕙 10:30", callback_data="time_1030")],
                [InlineKeyboardButton("🕝 14:30", callback_data="time_1430")],
                [InlineKeyboardButton("🕡 18:30", callback_data="time_1830")]
            ]
            await context.bot.send_message(
                chat_id=chat_id,
                text="🏠 Отлично, выбрали тематическую комнату!\n\n⏰ Какой слот времени удобен?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        elif query.data == "format_zone":
            try:
                await query.message.delete()
            except Exception:
                pass
            
            # Сохраняем выбранный формат
            lead = get_or_create_lead(query.from_user.id)
            update_lead_from_data(lead.id, {"format": "ресторан"})
            mark_lead_sent_to_manager(lead.id)
            
            # Отправляем в AmoCRM
            try:
                lead_data_for_crm = lead_to_dict(lead)
                result = await send_lead_to_amocrm(
                    lead_data_for_crm,
                    telegram_id=query.from_user.id,
                    username=query.from_user.username
                )
                if result and result[0]:
                    deal_id, contact_id = result
                    save_amocrm_deal_id(lead.id, deal_id)
                    if contact_id:
                        save_amocrm_contact_id(lead.id, contact_id)
            except Exception as e:
                logger.error(f"Failed to send to AmoCRM: {e}")
            
            # Уведомляем менеджера
            try:
                msg = format_lead_message("telegram", str(query.from_user.id), lead_to_dict(lead), query.from_user.username)
                await send_to_birthday_channel(msg)
            except Exception as e:
                logger.error(f"Failed to notify managers: {e}")
            
            # Показываем кнопки допуслуг (сразу, без слотов времени)
            keyboard = [
                [InlineKeyboardButton("🎂 Посмотреть торты", callback_data="view_cakes")],
                [InlineKeyboardButton("🎭 Аниматоры и шоу", callback_data="view_animators")],
                [InlineKeyboardButton("🎁 Пакеты под ключ", callback_data="view_packages")]
            ]
            await context.bot.send_message(
                chat_id=chat_id,
                text="🍰 Записал столик в ресторане!\n\n"
                     "Менеджер из отдела праздников скоро свяжется с вами 🧚\n\n"
                     "А пока вы ждёте — у нас есть своя кондитерская! 🎂\n"
                     "Можете выбрать торт к празднику или посмотреть другие услуги:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        
        # ============ ОБРАБОТКА СЛОТОВ ВРЕМЕНИ ============
        elif query.data in ("time_1030", "time_1430", "time_1830"):
            try:
                await query.message.delete()
            except Exception:
                pass
            
            time_map = {"time_1030": "10:30", "time_1430": "14:30", "time_1830": "18:30"}
            chosen_time = time_map[query.data]
            
            # Сохраняем время
            lead = get_or_create_lead(query.from_user.id)
            update_lead_from_data(lead.id, {"time": chosen_time})
            mark_lead_sent_to_manager(lead.id)
            
            # Отправляем в AmoCRM
            try:
                lead_data_for_crm = lead_to_dict(lead)
                result = await send_lead_to_amocrm(
                    lead_data_for_crm,
                    telegram_id=query.from_user.id,
                    username=query.from_user.username
                )
                if result and result[0]:
                    deal_id, contact_id = result
                    save_amocrm_deal_id(lead.id, deal_id)
                    if contact_id:
                        save_amocrm_contact_id(lead.id, contact_id)
            except Exception as e:
                logger.error(f"Failed to send to AmoCRM: {e}")
            
            # Уведомляем менеджера
            try:
                msg = format_lead_message("telegram", str(query.from_user.id), lead_to_dict(lead), query.from_user.username)
                await send_to_birthday_channel(msg)
            except Exception as e:
                logger.error(f"Failed to notify managers: {e}")
            
            # Показываем кнопки допуслуг (сразу, без подтверждения имени)
            keyboard = [
                [InlineKeyboardButton("🎂 Посмотреть торты", callback_data="view_cakes")],
                [InlineKeyboardButton("🎭 Аниматоры и шоу", callback_data="view_animators")],
                [InlineKeyboardButton("🎁 Пакеты под ключ", callback_data="view_packages")]
            ]
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⏰ Записал на {chosen_time}!\n\n"
                     f"Менеджер из отдела праздников скоро свяжется с вами 🧚\n\n"
                     f"А пока вы ждёте — у нас есть своя кондитерская! 🎂\n"
                     f"Можете выбрать торт к празднику или посмотреть другие услуги:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        
        # ============ ПОДТВЕРЖДЕНИЕ ИМЕНИ ============
        elif query.data == "name_confirm_yes":
            try:
                await query.message.delete()
            except Exception:
                pass
            
            # Сохраняем имя из профиля
            user_name = query.from_user.first_name or "Клиент"
            lead = get_or_create_lead(query.from_user.id)
            update_lead_from_data(lead.id, {"customer_name": user_name})
            
            # Обновляем сессию
            if session:
                ld = session.lead_data or {}
                ld["step"] = "done"
                session.lead_data = ld
                flag_modified(session, "lead_data")
                db.commit()
            
            # Получаем данные для итога
            lead_data_final = lead_to_dict(lead)
            
            # Отправляем в AmoCRM
            try:
                result = await send_lead_to_amocrm(
                    lead_data_final, 
                    telegram_id=query.from_user.id,
                    username=query.from_user.username
                )
                if result and result[0]:
                    deal_id, contact_id = result
                    save_amocrm_deal_id(lead.id, deal_id)
                    if contact_id:
                        save_amocrm_contact_id(lead.id, contact_id)
            except Exception as e:
                logger.error(f"Failed to send to AmoCRM: {e}")
            
            # Уведомляем менеджера
            try:
                msg = format_lead_message("telegram", str(query.from_user.id), lead_data_final, query.from_user.username)
                await send_to_birthday_channel(msg)
                mark_lead_sent_to_manager(lead.id)
            except Exception as e:
                logger.error(f"Failed to notify managers: {e}")
            
            # Показываем итог + допуслуги
            keyboard = [
                [InlineKeyboardButton("🎂 Каталог тортов", callback_data="view_cakes")],
                [InlineKeyboardButton("🎭 Аниматоры", callback_data="view_animators")]
            ]
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ Заявка принята! Менеджер скоро свяжется.\n\n"
                     f"📋 Ваша бронь:\n"
                     f"- Дата: {lead_data_final.get('event_date', '—')}\n"
                     f"- Детей: {lead_data_final.get('kids_count', '—')}\n"
                     f"- Формат: {lead_data_final.get('format', '—')}\n"
                     f"- Время: {lead_data_final.get('time', '—')}\n"
                     f"- Имя: {user_name}\n\n"
                     f"💡 Пока ждёте звонка:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        elif query.data == "name_confirm_no":
            try:
                await query.message.delete()
            except Exception:
                pass
            
            # Обновляем сессию
            if session:
                ld = session.lead_data or {}
                ld["step"] = "name_input"
                session.lead_data = ld
                flag_modified(session, "lead_data")
                db.commit()
            
            await context.bot.send_message(
                chat_id=chat_id,
                text="✏️ Напишите, пожалуйста, ваше имя:"
            )
        
        # ============ ПОДТВЕРЖДЕНИЕ ТЕЛЕФОНА ============
        elif query.data == "phone_confirm_yes":
            try:
                await query.message.delete()
            except Exception:
                pass
            
            # Получаем pending телефон из сессии
            pending_phone = None
            if session:
                ld = session.lead_data or {}
                pending_phone = ld.get("pending_phone")
            
            if pending_phone:
                lead = get_or_create_lead(query.from_user.id)
                update_lead_from_data(lead.id, {"phone": pending_phone})
                
                # Обновляем сессию
                if session:
                    ld = session.lead_data or {}
                    ld["step"] = "format"
                    ld["phone_confirmed"] = True
                    session.lead_data = ld
                    flag_modified(session, "lead_data")
                    db.commit()
                
                # Отправляем в AmoCRM
                lead_data_for_crm = lead_to_dict(lead)
                try:
                    result = await send_lead_to_amocrm(
                        lead_data_for_crm, 
                        telegram_id=query.from_user.id,
                        username=query.from_user.username
                    )
                    if result and result[0]:
                        save_amocrm_deal_id(lead.id, result[0])
                        if result[1]:
                            save_amocrm_contact_id(lead.id, result[1])
                except Exception as e:
                    logger.error(f"Failed to send to AmoCRM: {e}")
                
                # Показываем выбор формата
                event_date_str = lead_data_for_crm.get("event_date", "")
                kids_count = lead_data_for_crm.get("kids_count", 0)
                
                from core.utils import calculate_birthday_price
                event_date = parse_user_date(event_date_str)
                
                price_text = ""
                if event_date and kids_count:
                    room_calc = calculate_birthday_price(event_date, kids_count, "room", "nn")
                    zone_calc = calculate_birthday_price(event_date, kids_count, "zone", "nn")
                    price_text = (
                        f"🏠 Тематическая комната — 3 часа\n"
                        f"Для {kids_count} детей: {room_calc['description']}\n\n"
                        f"🍰 Столик в ресторане — без ограничений\n"
                        f"Для {kids_count} детей: {zone_calc['description']}\n\n"
                    )
                
                keyboard = [
                    [InlineKeyboardButton("🏠 Комната", callback_data="format_room")],
                    [InlineKeyboardButton("🍰 Ресторан", callback_data="format_zone")]
                ]
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"📞 Отлично, записал!\n\nДавайте выберем формат праздника! 💚\n\n{price_text}Какой формат вам ближе?",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        
        elif query.data == "phone_confirm_no":
            try:
                await query.message.delete()
            except Exception:
                pass
            
            # Обновляем сессию
            if session:
                ld = session.lead_data or {}
                ld["step"] = "phone_input"
                session.lead_data = ld
                flag_modified(session, "lead_data")
                db.commit()
            
            await context.bot.send_message(
                chat_id=chat_id,
                text="📱 Напишите, пожалуйста, ваш номер телефона для связи:"
            )
        
        # ============ ПРОСМОТР КАТАЛОГОВ ============
        elif query.data == "view_cakes":
            await context.bot.send_message(
                chat_id=chat_id,
                text="🎂 <b>Каталог тортов:</b>\n\n"
                     "👉 <a href='https://catalog.botcicada.ru/cakes.html'>Открыть каталог</a>\n\n"
                     "Закажите торт заранее — доставим прямо к празднику! 🍰",
                parse_mode="HTML"
            )
        
        elif query.data == "view_animators":
            await context.bot.send_message(
                chat_id=chat_id,
                text="🎭 <b>Аниматоры и программы:</b>\n\n"
                     "👉 <a href='https://catalog.botcicada.ru/animators.html'>Смотреть программы</a>\n\n"
                     "Наши аниматоры сделают праздник незабываемым! 🎉",
                parse_mode="HTML"
            )
        # ============ КОНЕЦ ОБРАБОТКИ ФОРМАТА ============
        
        
        elif query.data == "intent_general":
            if session:
                session.intent = "general"
                db.commit()
            
            # Удаляем старое сообщение с кнопками
            try:
                await query.message.delete()
            except Exception:
                pass
            
            # Отправляем фото с текстом
            caption = (
                "Отлично! 🎢\n\n"
                "Спрашивайте что угодно о парке:\n"
                "• Цены и режим работы\n"
                "• Аттракционы и развлечения\n"
                "• Скидки и акции\n"
                "• Как добраться\n\n"
                "Я с удовольствием помогу! 😊"
            )
            await send_photo_or_text(context.bot, chat_id, "general", caption)
            
        elif query.data == "intent_events":
            if session:
                session.intent = "events"
                db.commit()
            
            # Удаляем старое сообщение с кнопками
            try:
                await query.message.delete()
            except Exception:
                pass
            
            # Отправляем фото с текстом (динамически из afisha.txt)
            caption = get_afisha_events() or (
                "🎪 Афиша Джунгли Сити!\n\n"
                "Следите за нашими событиями:\n"
                "👉 nn.jucity.ru/afisha/"
            )
            await send_photo_or_text(context.bot, chat_id, "events", caption)
        
        elif query.data == "format_room":
            # Пользователь выбрал комнату → предлагаем слоты времени
            try:
                await query.message.delete()
            except Exception:
                pass
            
            # Сохраняем формат
            lead = db.query(Lead).filter(
                Lead.telegram_id == str(query.from_user.id),
                Lead.park_id == "nn",
                Lead.status.in_(["new", "contacted"])
            ).order_by(Lead.created_at.desc()).first()
            
            if lead:
                update_lead_from_data(lead.id, {"format": "room"})
            
            # Предлагаем выбрать время
            keyboard = [
                [InlineKeyboardButton("🕥 10:30", callback_data="time_10_30")],
                [InlineKeyboardButton("🕝 14:30", callback_data="time_14_30")],
                [InlineKeyboardButton("🕡 18:30", callback_data="time_18_30")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.send_message(
                chat_id=chat_id,
                text="🏠 Отлично, тематическая комната!\n\n"
                     "🕐 Выберите удобное время начала праздника:",
                reply_markup=reply_markup
            )
        
        elif query.data == "format_zone":
            # Пользователь выбрал ресторан → пропускаем слоты, сразу предлагаем услуги
            try:
                await query.message.delete()
            except Exception:
                pass
            
            # Сохраняем формат
            lead = db.query(Lead).filter(
                Lead.telegram_id == str(query.from_user.id),
                Lead.park_id == "nn",
                Lead.status.in_(["new", "contacted"])
            ).order_by(Lead.created_at.desc()).first()
            
            if lead:
                update_lead_from_data(lead.id, {"format": "zone"})
            
            # Предлагаем услуги
            keyboard = [
                [InlineKeyboardButton("🎂 Посмотреть торты", callback_data="show_cakes")],
                [InlineKeyboardButton("🎭 Аниматоры и шоу", callback_data="show_animators")],
                [InlineKeyboardButton("🎁 Пакеты под ключ", callback_data="show_packages")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.send_message(
                chat_id=chat_id,
                text="🍰 Отлично, столик в ресторане!\n\n"
                     "Менеджер из отдела праздников скоро свяжется с вами 📞\n\n"
                     "А пока вы ждёте — у нас есть кондитерская! 🎂\n"
                     "Можете выбрать торт к празднику или посмотреть другие услуги:",
                reply_markup=reply_markup
            )
        
        elif query.data.startswith("time_"):
            # Пользователь выбрал время → сохраняем и предлагаем услуги
            try:
                await query.message.delete()
            except Exception:
                pass
            
            # Парсим время
            time_map = {
                "time_10_30": "10:30",
                "time_14_30": "14:30",
                "time_18_30": "18:30"
            }
            selected_time = time_map.get(query.data, "")
            
            # Сохраняем время
            lead = db.query(Lead).filter(
                Lead.telegram_id == str(query.from_user.id),
                Lead.park_id == "nn",
                Lead.status.in_(["new", "contacted"])
            ).order_by(Lead.created_at.desc()).first()
            
            if lead:
                update_lead_from_data(lead.id, {"event_time": selected_time})
            
            # Предлагаем услуги
            keyboard = [
                [InlineKeyboardButton("🎂 Посмотреть торты", callback_data="show_cakes")],
                [InlineKeyboardButton("🎭 Аниматоры и шоу", callback_data="show_animators")],
                [InlineKeyboardButton("🎁 Пакеты под ключ", callback_data="show_packages")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⏰ Записал на {selected_time}!\n\n"
                     "Менеджер из отдела праздников скоро свяжется с вами 📞\n\n"
                     "А пока вы ждёте — у нас есть своя кондитерская! 🎂\n"
                     "Можете выбрать торт к празднику или посмотреть другие услуги:",
                reply_markup=reply_markup
            )
        
        elif query.data == "show_cakes":
            # Показываем каталог тортов
            await query.answer()
            await context.bot.send_message(
                chat_id=chat_id,
                text="🎂 <b>Наша кондитерская!</b>\n\n"
                     "У нас есть торты на любой вкус — от классических до тематических с персонажами!\n\n"
                     "📱 Посмотреть каталог: https://catalog.botcicada.ru/menu.html\n\n"
                     "Также можно принести свой торт (сбор 1000₽ за вынос торта).\n\n"
                     "Если нужна помощь с выбором — пишите, подскажу! 😊",
                parse_mode="HTML"
            )
        
        elif query.data == "show_animators":
            # Показываем информацию об аниматорах
            await query.answer()
            await context.bot.send_message(
                chat_id=chat_id,
                text="🎭 <b>Аниматоры и шоу!</b>\n\n"
                     "У нас есть:\n"
                     "• Тематические программы с персонажами\n"
                     "• Квесты и приключения\n"
                     "• Научные шоу\n"
                     "• Мастер-классы\n"
                     "• Аквагрим\n\n"
                     "📱 Каталог программ: https://catalog.botcicada.ru/animation.html\n\n"
                     "Менеджер поможет подобрать идеальную программу под возраст и интересы! 🎉",
                parse_mode="HTML"
            )
        
        elif query.data == "show_packages":
            # Показываем пакеты
            await query.answer()
            await context.bot.send_message(
                chat_id=chat_id,
                text="🎁 <b>Пакеты праздников под ключ!</b>\n\n"
                     "Готовые решения, чтобы вам не думать о деталях:\n\n"
                     "🌟 <b>Круто</b> — базовый комплект с аниматором\n"
                     "⭐ <b>Супер</b> — расширенный с шоу-программой\n"
                     "🔥 <b>WOW</b> — максимальный с VIP-обслуживанием\n\n"
                     "📱 Подробнее: https://catalog.botcicada.ru/packages.html\n\n"
                     "Или расскажите мне что хотите — и я помогу выбрать! 😊",
                parse_mode="HTML"
            )
        
        elif query.data == "my_booking":
            # Кнопка "Моё бронирование" из стартового меню
            try:
                await query.message.delete()
            except Exception:
                pass
            
            # Сначала пробуем найти контакт в AmoCRM по telegram_id
            contact = await amocrm_client.find_contact_by_telegram_id(query.from_user.id)
            
            if contact:
                # Получаем все сделки этого контакта из AmoCRM
                deals = await amocrm_client.get_deals_for_contact(contact["id"])
                
                if deals:
                    for deal in deals[:3]:  # Показываем до 3х последних
                        # Форматируем инфо из AmoCRM
                        text = (
                            f"📋 <b>Бронирование #{deal.get('deal_id')}</b>\n\n"
                            f"📅 Дата: {deal.get('event_date', 'Не указана')}\n"
                            f"🕐 Время: {deal.get('event_time', 'Не указано')}\n"
                            f"👶 Детей: {deal.get('kids_count', 'Не указано')}\n"
                            f"👨‍👩‍👧 Взрослых: {deal.get('adults_count', 0)}\n"
                            f"🏠 Комната: {deal.get('room', 'Не выбрана')}\n"
                            f"🎁 Доп. услуги: {deal.get('extras', 'Нет')}\n"
                        )
                        
                        # Ищем lead_id в локальной БД для кнопок изменения
                        local_lead = db.query(Lead).filter(
                            Lead.amocrm_deal_id == str(deal["deal_id"])
                        ).first()
                        lead_id = local_lead.id if local_lead else deal["deal_id"]
                        
                        keyboard = [
                            [InlineKeyboardButton("✏️ Изменить дату/время", callback_data=f"change_{lead_id}_datetime")],
                            [InlineKeyboardButton("👥 Изменить кол-во гостей", callback_data=f"change_{lead_id}_guests")],
                            [InlineKeyboardButton("🎁 Добавить услуги", callback_data=f"change_{lead_id}_extras")],
                            [InlineKeyboardButton("❌ Отменить бронь", callback_data=f"change_{lead_id}_cancel")]
                        ]
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=text,
                            reply_markup=InlineKeyboardMarkup(keyboard),
                            parse_mode="HTML"
                        )
                    return
            
            # Fallback: ищем в локальной БД
            leads = db.query(Lead).filter(
                Lead.telegram_id == str(query.from_user.id),
                Lead.status.in_(["new", "contacted", "booked"]),
                Lead.sent_to_manager == True
            ).order_by(Lead.created_at.desc()).limit(3).all()
            
            if not leads:
                keyboard = [
                    [InlineKeyboardButton("🎉 Забронировать праздник", callback_data="intent_birthday")]
                ]
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="📋 У вас пока нет активных бронирований.\n\n"
                         "Хотите организовать незабываемый день рождения? 🎂",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                for lead in leads:
                    text = format_booking_info(lead)
                    keyboard = [
                        [InlineKeyboardButton("✏️ Изменить дату/время", callback_data=f"change_{lead.id}_datetime")],
                        [InlineKeyboardButton("👥 Изменить кол-во гостей", callback_data=f"change_{lead.id}_guests")],
                        [InlineKeyboardButton("🎁 Добавить услуги", callback_data=f"change_{lead.id}_extras")],
                        [InlineKeyboardButton("❌ Отменить бронь", callback_data=f"change_{lead.id}_cancel")]
                    ]
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode="HTML"
                    )
        
        elif query.data.startswith("change_"):
            # Обработка запросов на изменение бронирования
            parts = query.data.split("_")
            lead_id = int(parts[1])
            change_type = parts[2]
            
            change_type_text = {
                "datetime": "📅 Изменить дату/время",
                "guests": "👥 Изменить количество гостей",
                "extras": "🎁 Добавить услуги",
                "cancel": "❌ Отменить бронирование"
            }
            
            # Получаем информацию о лиде
            lead = db.query(Lead).filter(Lead.id == lead_id).first()
            
            if lead:
                # Формируем уведомление менеджеру
                msg_text = (
                    f"⚠️ <b>Запрос на изменение бронирования</b>\n\n"
                    f"📋 Заявка: #{lead.id}\n"
                    f"🔄 Тип: {change_type_text.get(change_type, change_type)}\n\n"
                    f"👤 Клиент: {lead.customer_name or 'Не указано'}\n"
                    f"📞 Телефон: {lead.phone or 'Не указан'}\n"
                    f"💬 Telegram: @{query.from_user.username or 'нет username'}\n\n"
                    f"📅 Текущая дата: {lead.event_date or 'Не указана'}\n"
                    f"⏰ Время: {lead.time or 'Не указано'}\n"
                    f"👶 Детей: {lead.kids_count or 0}"
                )
                await send_to_managers(msg_text)
                
                # Создаём задачу в AmoCRM если есть сделка
                if lead.amocrm_deal_id:
                    try:
                        task_text = f"Клиент просит: {change_type_text.get(change_type, change_type)} (из Telegram)"
                        await amocrm_client.create_task(int(lead.amocrm_deal_id), task_text)
                    except Exception as e:
                        logger.error(f"Failed to create AmoCRM task: {e}")
                
                # Отвечаем пользователю
                if change_type == "cancel":
                    response_text = (
                        "❌ Запрос на отмену бронирования передан менеджеру.\n\n"
                        "Наши феи праздников свяжутся с вами в ближайшее время для подтверждения."
                    )
                else:
                    response_text = (
                        f"✅ Запрос на изменение передан менеджеру!\n\n"
                        f"Тип изменения: {change_type_text.get(change_type, change_type)}\n\n"
                        f"Наши феи праздников свяжутся с вами в ближайшее время. 💚"
                    )
                
                await query.message.reply_text(response_text)
            else:
                await query.message.reply_text(
                    "К сожалению, бронирование не найдено. Попробуйте /booking ещё раз."
                )

        elif query.data == "lost_phone_yes":
            # Подтвердил телефон — отправляем уведомление
            lost_data = session.lead_data or {}
            user = query.from_user
            user_name = user.first_name or "Гость"
            
            msg = format_lost_item_message(
                platform="telegram",
                user_id=str(user.id),
                user_name=user_name,
                lost_date=lost_data.get("lost_date"),
                lost_location=lost_data.get("lost_location"),
                lost_description=lost_data.get("lost_description"),
                phone=lost_data.get("phone"),
                username=user.username
            )
            await send_to_managers(msg)
            
            # Сбрасываем режим
            session.intent = "unknown"
            session.lead_data = {}
            db.commit()
            
            await query.message.reply_text(
                "✅ Спасибо! Мы передали информацию в бюро находок.\n\n"
                "Менеджер свяжется с вами, если вещь найдётся. 💚"
            )

        elif query.data == "lost_phone_no":
            # Не подтвердил — запрашиваем новый номер
            lost_data = session.lead_data or {}
            lost_data["lost_step"] = "phone"
            lost_data.pop("phone", None)
            session.lead_data = lost_data
            flag_modified(session, "lead_data")
            db.commit()
            
            await query.message.reply_text("📱 Укажите номер телефона для связи:")

        elif query.data == "lost_confirm_yes":
            # Подтвердил что потерял вещь — начинаем опрос
            lost_data = {"lost_step": "date"}
            session.intent = "lost_item"
            session.lead_data = lost_data
            flag_modified(session, "lead_data")
            db.commit()
            
            await query.message.reply_text(
                "Ой, как жаль! 😔 Давайте попробуем найти вашу вещь.\n\n"
                "📅 Когда вы были в парке? (напишите дату)"
            )

        elif query.data == "lost_confirm_no":
            # Не потерял — сбрасываем режим и обрабатываем исходное сообщение
            original_message = (session.lead_data or {}).get("original_message", "")
            
            session.intent = "unknown"
            session.lead_data = {}
            db.commit()
            
            if original_message:
                # Обрабатываем исходное сообщение через AI
                from core.message_service import get_message_service, UserInfo, Platform
                
                user = query.from_user
                user_info = UserInfo(
                    user_id=str(user.id),
                    platform=Platform.TELEGRAM,
                    username=user.username,
                    first_name=user.first_name
                )
                
                service = get_message_service("nn")
                result = await service.process_message(user_info, original_message)
                
                await query.message.reply_text(
                    result.text,
                    parse_mode="HTML"
                )
            else:
                await query.message.reply_text(
                    "Понял! 😊 Тогда чем могу помочь?\n\n"
                    "Спрашивайте о парке, ценах или празднике! 💚"
                )


        elif query.data == "photo_phone_yes":
            # Подтвердил телефон — создаём заявку на фотосессию
            photo_data = session.lead_data or {}
            user = query.from_user
            user_name = user.first_name or "Гость"
            phone = photo_data.get("phone", "")
            photo_date = photo_data.get("photo_date", "Не указана")
            
            # Отправляем уведомление менеджерам
            msg = format_photo_order_message(
                platform="telegram",
                user_id=str(user.id),
                user_name=user_name,
                phone=phone,
                username=user.username
            )
            # Добавляем дату в сообщение
            msg = msg.replace("</b>\n\n", f"</b>\n\n📅 <b>Дата:</b> {photo_date}\n\n", 1)
            await send_to_managers(msg)
            
            # Создаём заявку в AmoCRM
            try:
                await send_lead_to_amocrm(
                    lead_data={
                        "customer_name": user_name,
                        "phone": phone,
                        "event_date": photo_date,
                        "extras": "📸 Заказ фотографа (2500₽/час)",
                        "source": "telegram"
                    },
                    telegram_id=user.id,
                    username=user.username
                )
            except Exception as e:
                logger.error(f"Error creating photo order lead: {e}")
            
            # Сбрасываем режим
            session.intent = "unknown"
            session.lead_data = {}
            db.commit()
            
            await query.message.reply_text(
                f"📸 Отлично! Записали на {photo_date}.\n\n"
                "Мы передали вашу заявку в отдел праздников — вам перезвонят! 💚"
            )

        elif query.data == "photo_phone_no":
            # Не подтвердил телефон — запрашиваем новый номер
            photo_data = session.lead_data or {}
            photo_data["photo_step"] = "phone"
            photo_data.pop("phone", None)
            session.lead_data = photo_data
            flag_modified(session, "lead_data")
            db.commit()
            
            await query.message.reply_text("📱 Укажите номер телефона для связи:")

        # ============ ПРОСМОТР КАТАЛОГОВ ДР ============
        elif query.data == "view_cakes":
            await query.message.reply_text(
                "🎂 Наша кондитерская!\n\n"
                "У нас есть торты на любой вкус — от классических до тематических с персонажами!\n\n"
                "📱 Посмотреть каталог: https://catalog.botcicada.ru/menu.html\n\n"
                "Также можно принести свой торт (сбор 1000₽ за вынос торта).\n\n"
                "Если нужна помощь с выбором — пишите, подскажу! 😊"
            )

        elif query.data == "view_animators":
            await query.message.reply_text(
                "🎭 Аниматоры и шоу!\n\n"
                "У нас есть:\n"
                "• Тематические программы с персонажами\n"
                "• Квесты и приключения\n"
                "• Научные шоу\n"
                "• Мастер-классы\n"
                "• Аквагрим\n\n"
                "📱 Каталог программ: https://catalog.botcicada.ru/animation.html\n\n"
                "Менеджер поможет подобрать идеальную программу под возраст и интересы! 🎉"
            )

        elif query.data == "view_packages":
            await query.message.reply_text(
                "🎁 Пакеты праздников!\n\n"
                "🌴 «Джунгли зовут» (5 детей) — от 9 660₽\n"
                "Билеты + поздравление от Джуси + угощения\n\n"
                "🦁 «Большое сафари» (7 детей) — от 16 050₽\n"
                "Билеты + анимация 60 мин + угощения\n\n"
                "🌴 «Тропический переполох» (10 детей) — от 25 850₽\n"
                "Билеты + анимация + мини-шоу + шары + угощения\n\n"
                "📱 Подробнее: https://catalog.botcicada.ru/packages.html\n\n"
                "Расскажите что хотите — и я помогу выбрать! 😊"
            )

    finally:

        db.close()


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработчик текстовых сообщений.
    
    Использует MessageService для унифицированной обработки.
    Вся бизнес-логика теперь в core/message_service.py и core/flows/.
    """
    from core.message_service import get_message_service, UserInfo, Platform
    from core.notifications import send_to_managers, send_to_birthday_channel
    
    user = update.effective_user
    message_text = update.message.text
    
    logger.info(f"Message from {user.first_name} ({user.id}): {message_text}")
    
    # Создаём UserInfo
    user_info = UserInfo(
        user_id=str(user.id),
        platform=Platform.TELEGRAM,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
    )
    
    try:
        # Получаем MessageService
        service = get_message_service("nn")
        
        # Обрабатываем сообщение через унифицированный сервис
        result = await service.process_message(user_info, message_text)
        
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
        
        # Отправляем ответ
        if result.image_path and os.path.exists(result.image_path):
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
        
        # Уведомляем менеджеров если нужно
        if result.should_notify_manager and result.manager_message:
            try:
                if result.manager_channel == "birthday":
                    await send_to_birthday_channel(result.manager_message)
                else:
                    await send_to_managers(result.manager_message)
            except Exception as e:
                logger.error(f"Failed to notify managers: {e}")
                
    except Exception as e:
        logger.error(f"Error handling message: {e}", exc_info=True)
        await update.message.reply_text(
            "Упс, что-то пошло не так! 😅 Попробуйте ещё раз или напишите /human для связи с менеджером."
        )
async def notify_manager(update: Update, lead, context: ContextTypes.DEFAULT_TYPE):
    """Отправить уведомление менеджеру о новом лиде."""
    if not MANAGER_CHAT_ID:
        logger.warning("MANAGER_CHAT_ID not configured, skipping notification")
        return
    
    user = update.effective_user
    summary = lead.get_summary()
    summary += f"\n\n📱 Telegram: @{user.username}" if user.username else f"\n\n📱 Telegram ID: {user.id}"
    
    try:
        await context.bot.send_message(
            chat_id=MANAGER_CHAT_ID,
            text=summary,
            parse_mode="Markdown"
        )
        logger.info(f"Manager notified about lead from {user.id}")
    except Exception as e:
        logger.error(f"Failed to notify manager: {e}")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок."""
    import traceback
    with open("/root/jungle_bot/crash.log", "a") as f:
        f.write(f"\nGLOBAL ERROR handler at {datetime.now()}:\n")
        f.write(f"Update: {update}\n")
        f.write(f"Error: {context.error}\n")
        traceback.print_exception(type(context.error), context.error, context.error.__traceback__, file=f)
        
    logger.error(f"Update {update} caused error {context.error}")
