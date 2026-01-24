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


from core.utils import get_prices_from_knowledge, get_afisha_events


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
    prices = get_prices_from_knowledge()
    
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
    
    # Стандартное приветствие для НОВОЙ заявки
    await update.message.reply_text(
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
        "📅 <b>На какую дату планируете праздник?</b>",
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
            with open(IMAGES["birthday"], 'rb') as photo_file:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_file,
                    caption=caption,
                    parse_mode="HTML"
                )

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
                    
                    # Сохраняем телефон и lead_id в context для подтверждения
                    context.user_data["pending_phone_confirm"] = found_phone
                    context.user_data["pending_lead_id"] = current_lead.id
                    context.user_data["pending_customer_name"] = found_name
                    
                    logger.info(f"Found returning customer: {found_name}, phone={found_phone}, asking for confirmation")
                    
                    # Спрашиваем подтверждение телефона
                    phone_display = f"+7 {found_phone[-10:-7]} {found_phone[-7:-4]}-{found_phone[-4:-2]}-{found_phone[-2:]}" if len(found_phone) >= 10 else found_phone
                    keyboard = [
                        [InlineKeyboardButton(f"✅ Да, {phone_display}", callback_data="confirm_returning_phone_yes")],
                        [InlineKeyboardButton("📱 Указать другой номер", callback_data="confirm_returning_phone_no")]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    greeting = f"Рады снова видеть вас, {found_name}! 💚\n\n" if found_name else "Рады снова вас видеть! 💚\n\n"
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"{greeting}📱 Актуален ли этот номер телефона для связи?\n\n{phone_display}",
                        reply_markup=reply_markup
                    )
                    return  # Ждём подтверждения
            
            # Если контакт не найден или нет телефона — стандартный флоу
            # Отправляем фото с текстом
            caption = (
                "💜💚 Отлично! День рождения в Джунглях — это радость и вау-эмоции! 💚💜\n\n"
                "У нас есть 2 формата праздника — выбирайте, что подойдёт именно вам 💚\n\n"
                "🏠 ТЕМАТИЧЕСКАЯ КОМНАТА (3 часа)\n"
                "—предоставляется при оплате 6 полных детских билетов\n"
                "— от 7 детей — ИМЕНИННИК БЕСПЛАТНО\n"
                "— безлимит на аттракционы 💚\n\n"
                "🍰 Столик в ресторане\n"
                "— без ограничения по времени\n"
                "— именинник — скидка 50% на вход\n"
                "— безлимит на аттракционы 💚\n\n"
                "✨ Аниматоры, торт, шары, аквагрим — по желанию.\n"
                "Давайте подберём идеальный вариант для вас 💜\n\n"
                "📅 На какую дату планируете праздник?"
            )
            with open(IMAGES["birthday"], 'rb') as photo_file:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_file,
                    caption=caption
                )
        
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
            
            # Очищаем pending
            context.user_data.pop("pending_phone_confirm", None)
            context.user_data.pop("pending_lead_id", None)
            
            await context.bot.send_message(
                chat_id=chat_id,
                text="✅ Отлично! Заявка отправлена феям праздников! 🧚‍♀️\n\nДавайте выберем формат праздника? 💚"
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
            
            # Очищаем pending
            context.user_data.pop("pending_phone_confirm", None)
            context.user_data.pop("pending_lead_id", None)
            context.user_data.pop("pending_customer_name", None)
            
            # Отправляем стандартное сообщение о бронировании
            caption = (
                "💜💚 Отлично! День рождения в Джунглях — это радость и вау-эмоции! 💚💜\n\n"
                "У нас есть 2 формата праздника — выбирайте, что подойдёт именно вам 💚\n\n"
                "🏠 ТЕМАТИЧЕСКАЯ КОМНАТА (3 часа)\n"
                "—предоставляется при оплате 6 полных детских билетов\n"
                "— от 7 детей — ИМЕНИННИК БЕСПЛАТНО\n"
                "— безлимит на аттракционы 💚\n\n"
                "🍰 Столик в ресторане\n"
                "— без ограничения по времени\n"
                "— именинник — скидка 50% на вход\n"
                "— безлимит на аттракционы 💚\n\n"
                "✨ Аниматоры, торт, шары, аквагрим — по желанию.\n"
                "Давайте подберём идеальный вариант для вас 💜\n\n"
                "📅 На какую дату планируете праздник?"
            )
            with open(IMAGES["birthday"], 'rb') as photo_file:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_file,
                    caption=caption
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
            with open(IMAGES["general"], 'rb') as photo_file:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_file,
                    caption=caption
                )
            
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
            with open(IMAGES["events"], 'rb') as photo_file:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_file,
                    caption=caption
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

    finally:
        db.close()


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений."""
    user = update.effective_user
    message_text = update.message.text
    user_id = str(user.id)
    
    logger.info(f"Message from {user.first_name} ({user_id}): {message_text}")
    
    db = SessionLocal()
    try:
        # Получаем или создаём сессию
        session = db.query(DBSession).filter(DBSession.telegram_id == user_id).first()
        if not session:
            session = DBSession(telegram_id=user_id, park_id="nn", username=user.username)
            db.add(session)
            db.commit()
            db.refresh(session)
        else:
            # Обновляем username если изменился
            if user.username and session.username != user.username:
                session.username = user.username
                db.commit()
        
        # Сохраняем сообщение пользователя
        user_message = Message(session_id=session.id, role="user", content=message_text)
        db.add(user_message)
        db.commit()
        
        # Получаем lead_data из сессии
        lead_data = session.lead_data or {}
        verified_name = None  # Персонализация отключена

        # --- НОВАЯ ЛОГИКА: Проверка на ID приложения ---
        # Ищем только если есть явное упоминание "id", "код" и НЕТ признаков телефона
        app_id_match = None
        
        # Исключаем очевидные телефонные паттерны
        # Телефоны: +7, 7, 8, 9 + 10-11 цифр всего, или содержат +, (), -
        is_phone_pattern = bool(re.search(r'[\+\(\)]', message_text))
        is_phone_pattern = is_phone_pattern or bool(re.search(r'\d{1,3}\-\d{1,3}\-\d{1,3}', message_text))
        # Также проверим: число 10-11 цифр, начинающееся с 7, 8, 9 — это телефон
        phone_like_number = re.search(r'\b([789]\d{9,10})\b', message_text)
        
        if not is_phone_pattern:
            # Ключевые слова для ID
            id_keywords = r'(?:app\s*id|мой\s*id|айди|ид\b|код|подписал\w*|подписка)'
            
            # Сначала ищем ID с ключевым словом
            app_id_match = re.search(
                id_keywords + r'\s*[,:.=\-]?\s*(\d{5,8})\b', 
                message_text, re.IGNORECASE
            )
            
            # Если с ключевым словом нашли число, похожее на телефон — отклоняем
            if app_id_match and phone_like_number:
                if app_id_match.group(1) == phone_like_number.group(1):
                    app_id_match = None
            
            # Если не нашли с ключевым словом — ищем "голое" 5-8 значное число
            if not app_id_match:
                clean_text = message_text.strip()
                # Только короткое сообщение, и число НЕ начинается с 7, 8, 9 (чтобы не путать с телефоном)
                if len(clean_text) <= 12:
                    bare_id_match = re.match(r'^(\d{5,8})$', clean_text)
                    if bare_id_match:
                        potential_id = bare_id_match.group(1)
                        # Если начинается с 7, 8, 9 и 10+ цифр — это телефон
                        if not (potential_id[0] in '789' and len(potential_id) >= 10):
                            app_id_match = bare_id_match
        
        if app_id_match:
            app_id = app_id_match.group(1)
            
            # Отправляем уведомление менеджерам
            try:
                msg_text = (
                    f"🔔 <b>Новый App ID!</b>\n\n"
                    f"👤 Пользователь: {user.first_name or 'Неизвестный'} (@{user.username or 'нет username'})\n"
                    f"🔢 ID: <code>{app_id}</code>\n"
                    f"💬 Сообщение: {message_text}"
                )
                await send_to_managers(msg_text)
                logger.info(f"App ID {app_id} notification sent to manager")
            except Exception as e:
                logger.error(f"Failed to notify manager about App ID: {e}")
            
            # Отвечаем пользователю
            await update.message.reply_text(
                "Принято! Передал менеджеру для начисления баллов. "
                "Баллы будут начислены в течение 7 дней. "
                "Спасибо, что вы с нами! 💚💜"
            )
            return
        # -----------------------------------------------
        
        # ============ ЖАЛОБЫ — обработка жалоб на обслуживание ============
        # Проверяем, находимся ли мы уже в режиме опроса жалобы
        complaint_data = session.lead_data or {}
        complaint_step = complaint_data.get("complaint_step")
        
        if complaint_step:
            # Мы в процессе сбора телефона для жалобы
            if complaint_step == "phone":
                # Валидируем телефон
                phone_pattern = r'[\d\+\(\)\-\s]{7,}'
                if re.search(phone_pattern, message_text):
                    complaint_data["phone"] = message_text
                    
                    # Отправляем уведомление менеджерам
                    msg = format_complaint_message(
                        platform="telegram",
                        user_id=user_id,
                        user_name=user.first_name or "Гость",
                        complaint_text=complaint_data.get("complaint_text", ""),
                        phone=message_text,
                        username=user.username
                    )
                    await send_to_managers(msg)
                    
                    # Сбрасываем режим
                    session.intent = "unknown"
                    session.lead_data = {}
                    db.commit()
                    
                    await update.message.reply_text(
                        "🙏 Спасибо, что сообщили нам об этом!\n\n"
                        "Информация передана руководству парка. "
                        "Мы обязательно разберёмся в ситуации и свяжемся с вами "
                        "в ближайшее время для решения вопроса.\n\n"
                        "Приносим извинения за доставленные неудобства. 💚"
                    )
                    return
                else:
                    await update.message.reply_text("📱 Пожалуйста, укажите корректный номер телефона:")
                    return
        
        # Проверяем триггер жалобы (ВАЖНО: проверяем ДО потерянных вещей!)
        if needs_complaint_flow(message_text):
            # Проверяем, есть ли телефон в CRM
            phone = None
            try:
                contact = await amocrm_client.find_contact_by_telegram_id(user.id)
                if contact:
                    contact_info = amocrm_client.get_contact_info(contact)
                    phone = contact_info.get("phone")
            except Exception as e:
                logger.error(f"Error finding contact for complaint: {e}")
            
            if phone:
                # Телефон уже есть — сразу отправляем жалобу
                msg = format_complaint_message(
                    platform="telegram",
                    user_id=user_id,
                    user_name=user.first_name or "Гость",
                    complaint_text=message_text,
                    phone=phone,
                    username=user.username
                )
                await send_to_managers(msg)
                
                await update.message.reply_text(
                    "😔 Нам очень жаль, что у вас остались негативные впечатления.\n\n"
                    "Информация передана руководству парка. "
                    "Мы обязательно разберёмся в ситуации и свяжемся с вами "
                    "в ближайшее время для решения вопроса.\n\n"
                    "Приносим извинения за доставленные неудобства. 💚"
                )
                return
            else:
                # Телефона нет — запрашиваем
                session.intent = "complaint"
                session.lead_data = {
                    "complaint_step": "phone",
                    "complaint_text": message_text  # Сохраняем текст жалобы
                }
                flag_modified(session, "lead_data")
                db.commit()
                
                await update.message.reply_text(
                    "😔 Нам очень жаль, что у вас остались негативные впечатления.\n\n"
                    "Мы обязательно разберёмся в ситуации!\n\n"
                    "📱 Пожалуйста, оставьте ваш номер телефона — "
                    "руководство парка свяжется с вами для решения вопроса."
                )
                return
        # ============ КОНЕЦ ЖАЛОБЫ ============
        
        # ============ ПОТЕРЯШКИ — обработка потерянных вещей ============
        # Проверяем, находимся ли мы уже в режиме опроса
        lost_data = session.lead_data or {}
        lost_step = lost_data.get("lost_step")
        
        if lost_step:
            # Проверяем, не хочет ли пользователь выйти из опроса
            exit_keywords = [
                "ничего не потерял", "ничего не потеряла", "ничего не теряла", "ничего не терял",
                "не потерял", "не потеряла", "не теряла", "не терял",
                "я не про это", "я о другом", "хотел спросить", "хотела спросить",
                "я спрашиваю", "речь не об этом", "не об этом",
                "отмена", "стоп", "хватит", "выход", "exit", "cancel",
                "можно купить", "где купить", "продаёте", "продаете",
            ]
            message_lower = message_text.lower()
            if any(kw in message_lower for kw in exit_keywords):
                # Сбрасываем режим потеряшек
                session.intent = "unknown"
                session.lead_data = {}
                db.commit()
                
                await update.message.reply_text(
                    "Ой, простите за недопонимание! 😊\n\n"
                    "Чем могу помочь? Спрашивайте — я отвечу на любые вопросы о парке, ценах или празднике! 💚"
                )
                return
            
            # Мы в процессе опроса о потерянной вещи
            user_name = user.first_name or "Гость"
            
            if lost_step == "date":
                lost_data["lost_date"] = message_text
                lost_data["lost_step"] = "location"
                session.lead_data = lost_data
                flag_modified(session, "lead_data")
                db.commit()
                await update.message.reply_text("📍 В каком примерно месте вы могли оставить вещь?\n(аттракцион, комната, ресторан и т.д.)")
                return
                
            elif lost_step == "location":
                lost_data["lost_location"] = message_text
                lost_data["lost_step"] = "description"
                session.lead_data = lost_data
                flag_modified(session, "lead_data")
                db.commit()
                await update.message.reply_text("🔍 Опишите, что именно потеряли?\n(цвет, размер, особенности)")
                return
                
            elif lost_step == "description":
                lost_data["lost_description"] = message_text
                lost_data["lost_step"] = "phone"
                session.lead_data = lost_data
                flag_modified(session, "lead_data")
                db.commit()
                
                # Проверяем телефон в CRM
                try:
                    contact = await amocrm_client.find_contact_by_telegram_id(user.id)
                    if contact:
                        contact_info = amocrm_client.get_contact_info(contact)
                        phone = contact_info.get("phone")
                        if phone:
                            lost_data["phone"] = phone
                            lost_data["lost_step"] = "confirm_phone"
                            session.lead_data = lost_data
                            flag_modified(session, "lead_data")
                            db.commit()
                            
                            keyboard = [
                                [InlineKeyboardButton("✅ Да", callback_data="lost_phone_yes"),
                                 InlineKeyboardButton("❌ Другой", callback_data="lost_phone_no")]
                            ]
                            await update.message.reply_text(
                                f"📱 Для связи использовать номер {phone}?",
                                reply_markup=InlineKeyboardMarkup(keyboard)
                            )
                            return
                except Exception as e:
                    logger.error(f"Failed to check CRM for lost item: {e}")
                
                # Нет телефона — запрашиваем
                await update.message.reply_text("📱 Укажите номер телефона для связи:")
                return
                
            elif lost_step == "phone":
                lost_data["phone"] = message_text
                
                # Отправляем уведомление
                msg = format_lost_item_message(
                    platform="telegram",
                    user_id=user_id,
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
                
                await update.message.reply_text(
                    "✅ Спасибо! Мы передали информацию в бюро находок.\n\n"
                    "Менеджер свяжется с вами, если вещь найдётся. 💚"
                )
                return
        
        # Проверяем триггер потеряшек (начало опроса)
        # Если уже был в режиме lost_item но lost_step нет — сбросим и начнём заново
        if session.intent == "lost_item" and not lost_step:
            session.intent = "unknown"
            db.commit()
        
        if needs_lost_item_flow(message_text):
            session.intent = "lost_item"
            session.lead_data = {"lost_step": "date"}
            flag_modified(session, "lead_data")
            db.commit()
            
            await update.message.reply_text(
                "Ой, как жаль! 😔 Давайте попробуем найти вашу вещь.\n\n"
                "📅 Когда вы были в парке? (напишите дату)"
            )
            return
        # ============ КОНЕЦ ПОТЕРЯШКИ ============
        
        # ============ АВТОПОКАЗ БРОНИРОВАНИЯ ============
        # Если пользователь спрашивает про своё бронирование — показываем
        booking_keywords = [
            "моя бронь", "моё бронь", "мое бронь", "мои брони",
            "моя заявка", "моё заявка", "мое заявка", "мои заявки",
            "моё бронирование", "мое бронирование", "мои бронирования",
            "статус заявки", "статус брони", "статус бронирования",
            "мой праздник", "моё праздник", "мое праздник",
            "что с заявкой", "что с бронью", "что с бронированием",
            "где моя заявка", "где моя бронь",
            "проверить бронь", "посмотреть бронь", "узнать статус"
        ]
        message_lower = message_text.lower()
        if any(kw in message_lower for kw in booking_keywords):
            # Вызываем функционал /booking
            leads = db.query(Lead).filter(
                Lead.telegram_id == user_id,
                Lead.status.in_(["new", "contacted", "booked"]),
                Lead.sent_to_manager == True
            ).order_by(Lead.created_at.desc()).limit(3).all()
            
            if leads:
                for lead in leads:
                    text = format_booking_info(lead)
                    keyboard = [
                        [InlineKeyboardButton("✏️ Изменить дату/время", callback_data=f"change_{lead.id}_datetime")],
                        [InlineKeyboardButton("👥 Изменить кол-во гостей", callback_data=f"change_{lead.id}_guests")],
                        [InlineKeyboardButton("🎁 Добавить услуги", callback_data=f"change_{lead.id}_extras")],
                        [InlineKeyboardButton("❌ Отменить бронь", callback_data=f"change_{lead.id}_cancel")]
                    ]
                    await update.message.reply_text(
                        text,
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode="HTML"
                    )
                return
            else:
                await update.message.reply_text(
                    "📋 У вас пока нет активных бронирований.\n\n"
                    "Хотите забронировать праздник? Напишите /birthday 🎉"
                )
                return
        # ============ КОНЕЦ АВТОПОКАЗА ============
        
        # Проверяем запрос живого менеджера
        if needs_human_escalation(message_text):
            # Отправляем уведомление менеджерам
            escalation_msg = format_escalation_message(
                platform="telegram",
                user_id=user_id,
                username=user.username,
                user_name=user.first_name or "Неизвестный",
                message=message_text
            )
            await send_to_managers(escalation_msg)
            
            # Отвечаем пользователю
            await update.message.reply_text(
                "Понимаю, что вам нужна помощь живого менеджера! 🙋\n\n"
                "Я уже передал ваш запрос нашей команде. "
                "Менеджер свяжется с вами в ближайшее время!\n\n"
                "А пока я могу ответить на ваши вопросы о парке или празднике. 😊"
            )
            return
        
        # ============ ЗАПРОСЫ НА ИЗМЕНЕНИЕ БРОНИРОВАНИЯ ============
        # Проверяем, просит ли клиент изменить/отменить бронь текстом
        if needs_booking_change_request(message_text):
            user_name = user.first_name or "Гость"
            change_type = get_booking_change_type(message_text)
            
            # Ищем сделку пользователя в AmoCRM
            deal_id = None
            phone = None
            try:
                contact = await amocrm_client.find_contact_by_telegram_id(user.id)
                if contact:
                    contact_info = amocrm_client.get_contact_info(contact)
                    phone = contact_info.get("phone")
                    
                    # Получаем последнюю сделку
                    deals = await amocrm_client.get_contact_deals(contact["id"])
                    if deals:
                        deal_id = str(deals[0].get("id", ""))
                        
                        # Создаём задачу в AmoCRM
                        task_text = f"Клиент просит: {change_type} (из Telegram)"
                        await amocrm_client.create_task(int(deal_id), task_text)
            except Exception as e:
                logger.error(f"Error checking AmoCRM for booking change: {e}")
            
            # Отправляем уведомление менеджерам
            msg = format_booking_change_message(
                platform="telegram",
                user_id=user_id,
                user_name=user_name,
                change_type=change_type,
                message_text=message_text,
                deal_id=deal_id,
                phone=phone,
                username=user.username
            )
            await send_to_birthday_channel(msg)
            
            # Отвечаем пользователю
            await update.message.reply_text(
                f"✅ Ваш запрос на «{change_type}» передан менеджеру!\n\n"
                "Мы свяжемся с вами в ближайшее время для уточнения деталей. 📞"
            )
            return
        # ============ КОНЕЦ ЗАПРОСОВ НА ИЗМЕНЕНИЕ ============
        
        # ============ ОБРАБОТКА ЗАПРОСОВ ФОТОГРАФИЙ ============
        # Проверяем, уже в режиме опроса про фото?
        photo_data = session.lead_data or {}
        photo_step = photo_data.get("photo_step")
        
        if photo_step:
            user_name = user.first_name or "Гость"
            
            if photo_step == "date":
                # Сохраняем дату и переходим к телефону
                photo_data["photo_date"] = message_text
                
                # Проверяем, есть ли уже телефон
                if photo_data.get("phone"):
                    # Телефон уже есть — подтверждаем
                    phone = photo_data["phone"]
                    photo_data["photo_step"] = "confirm_phone"
                    session.lead_data = photo_data
                    flag_modified(session, "lead_data")
                    db.commit()
                    
                    keyboard = [
                        [InlineKeyboardButton("✅ Да, верно", callback_data="photo_phone_yes")],
                        [InlineKeyboardButton("📱 Другой номер", callback_data="photo_phone_no")]
                    ]
                    await update.message.reply_text(
                        f"📱 Для связи использовать номер {phone}?",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                else:
                    # Телефона нет — спрашиваем
                    photo_data["photo_step"] = "phone"
                    session.lead_data = photo_data
                    flag_modified(session, "lead_data")
                    db.commit()
                    
                    await update.message.reply_text("📱 Укажите номер телефона для связи:")
                return
            
            elif photo_step == "phone":
                # Валидируем телефон
                phone_pattern = r'[\d\+\(\)\-\s]{7,}'
                if re.search(phone_pattern, message_text):
                    photo_data["phone"] = message_text
                    photo_type = photo_data.get("type", "request")
                    
                    if photo_type in ["order", "standalone"]:
                        # Заказ фотографа — создаём заявку
                        photo_date = photo_data.get("photo_date", "Не указана")
                        
                        msg = format_photo_order_message(
                            platform="telegram",
                            user_id=user_id,
                            user_name=user_name,
                            phone=message_text,
                            username=user.username
                        )
                        # Добавляем дату в сообщение для менеджеров
                        msg = msg.replace("</b>\n\n", f"</b>\n\n📅 <b>Дата:</b> {photo_date}\n\n", 1)
                        await send_to_managers(msg)
                        
                        # Создаём лид и отправляем в AmoCRM
                        try:
                            lead = get_or_create_lead(user_id, source="telegram", park_id="nn")
                            lead.phone = message_text
                            lead.name = user_name
                            lead.extras = "Фотограф (2500₽/час)"
                            db.commit()
                            
                            # Отправляем в AmoCRM
                            await send_lead_to_amocrm(
                                lead_data={
                                    "customer_name": user_name,
                                    "phone": message_text,
                                    "event_date": photo_date,
                                    "extras": "📸 Заказ фотографа (2500₽/час)",
                                    "source": "telegram"
                                },
                                telegram_id=user.id,
                                username=user.username
                            )
                        except Exception as e:
                            logger.error(f"Error creating photo order lead: {e}")
                        
                        await update.message.reply_text(
                            f"📸 Отлично! Записали на {photo_date}.\n\n"
                            "Мы передали вашу заявку в отдел праздников — вам перезвонят! 💚"
                        )
                    else:
                        # Запрос фото — уведомление менеджерам
                        msg = format_photo_request_message(
                            platform="telegram",
                            user_id=user_id,
                            user_name=user_name,
                            phone=message_text,
                            description=photo_data.get("description"),
                            username=user.username
                        )
                        await send_to_managers(msg)
                        
                        await update.message.reply_text(
                            "📷 Спасибо! Мы передали ваш запрос.\n\n"
                            "Менеджер свяжется с вами по поводу фотографий! 💚"
                        )
                    
                    # Сбрасываем режим
                    session.intent = "unknown"
                    session.lead_data = {}
                    db.commit()
                    return
                else:
                    await update.message.reply_text("📱 Пожалуйста, укажите корректный номер телефона:")
                    return
        
        # Проверяем триггер заказа фотографа (ТОЛЬКО если нет активного бронирования!)
        # Если есть активный лид — пусть AI-агент обработает как extras
        if needs_photo_order(message_text):
            # Проверяем, есть ли у пользователя активное бронирование
            active_lead_info = get_active_lead_info(user_id)
            has_active_booking = active_lead_info and active_lead_info.get("event_date")
            
            if has_active_booking:
                # Есть активное бронирование — пропускаем, пусть AI-агент добавит в extras
                # или обработает через новую логику deal_in_work
                logger.info(f"Photo order detected but user has active booking, passing to AI agent")
                pass  # НЕ делаем return — пусть идёт дальше к AI-агенту
            else:
                # Нет активного бронирования — самостоятельный флоу фотосессии
                user_name = user.first_name or "Гость"
                
                # Проверяем телефон в CRM
                phone = None
                try:
                    contact = await amocrm_client.find_contact_by_telegram_id(user.id)
                    if contact:
                        contact_info_crm = amocrm_client.get_contact_info(contact)
                        phone = contact_info_crm.get("phone")
                except Exception as e:
                    logger.error(f"Error finding contact for photo order: {e}")
                
                # Начинаем опрос: сначала дата, потом телефон
                session.intent = "photo_order"
                session.lead_data = {
                    "photo_step": "date",  # Сначала спрашиваем дату
                    "type": "standalone",  # Самостоятельная фотосессия
                    "phone": phone  # Если нашли — сохраняем
                }
                flag_modified(session, "lead_data")
                db.commit()
                
                await update.message.reply_text(
                    "📸 Отличная идея! Фотографии получаются яркие и эмоциональные — отличная память!\n\n"
                    "💰 Стоимость фотографа: 2500₽/час\n\n"
                    "📅 На какую дату планируете фотосессию?"
                )
                return
        
        # Проверяем триггер запроса фотографий (получение готовых фото)
        if needs_photo_request(message_text):
            user_name = user.first_name or "Гость"
            
            # Проверяем телефон в CRM
            phone = None
            try:
                contact = await amocrm_client.find_contact_by_telegram_id(user.id)
                if contact:
                    contact_info_crm = amocrm_client.get_contact_info(contact)
                    phone = contact_info_crm.get("phone")
            except Exception as e:
                logger.error(f"Error finding contact for photo request: {e}")
            
            if phone:
                # Телефон есть — сразу отправляем уведомление
                msg = format_photo_request_message(
                    platform="telegram",
                    user_id=user_id,
                    user_name=user_name,
                    phone=phone,
                    description=message_text[:200],
                    username=user.username
                )
                await send_to_managers(msg)
                
                await update.message.reply_text(
                    "📷 Понимаю, что вы ждёте свои фотографии!\n\n"
                    "Мы передали ваш запрос, с вами свяжутся в ближайшее время. 💚"
                )
            else:
                # Телефона нет — запрашиваем
                session.intent = "photo_request"
                session.lead_data = {"photo_step": "phone", "type": "request", "description": message_text[:200]}
                flag_modified(session, "lead_data")
                db.commit()
                
                await update.message.reply_text(
                    "📷 Понимаю, что вы ждёте свои фотографии!\n\n"
                    "📱 Оставьте ваш номер телефона, чтобы мы могли связаться с вами:"
                )
            return
        # ============ КОНЕЦ ОБРАБОТКИ ФОТОГРАФИЙ ============
        
        # ============ ОБРАБОТКА ПРЕДЛОЖЕНИЙ О СОТРУДНИЧЕСТВЕ ============
        partnership_data = session.lead_data if session.lead_data else {}
        partnership_step = partnership_data.get("partnership_step")
        
        # Если уже в процессе опроса по предложению
        if partnership_step == "details":
            # Получили суть предложения, запрашиваем телефон
            session.lead_data = {
                "partnership_step": "phone",
                "proposal_text": message_text[:500]
            }
            flag_modified(session, "lead_data")
            db.commit()
            
            await update.message.reply_text(
                "📝 Отлично, записал!\n\n"
                "📱 Оставьте, пожалуйста, ваш номер телефона для связи:"
            )
            return
        
        if partnership_step == "phone":
            # Проверяем телефон
            phone_pattern = r'[\d\+\(\)\-\s]{7,}'
            if re.search(phone_pattern, message_text):
                # Отправляем уведомление менеджерам
                msg = format_partnership_message(
                    platform="telegram",
                    user_id=user_id,
                    user_name=user_name,
                    proposal_text=partnership_data.get("proposal_text", ""),
                    phone=message_text,
                    username=user.username
                )
                await send_to_managers(msg)
                
                # Сбрасываем состояние
                session.intent = "unknown"
                session.lead_data = {}
                db.commit()
                
                await update.message.reply_text(
                    "🤝 Спасибо за ваше предложение!\n\n"
                    "Мы передали его руководству. С вами свяжутся в ближайшее время! 💚"
                )
                return
            else:
                await update.message.reply_text("📱 Пожалуйста, укажите корректный номер телефона:")
                return
        
        # Проверяем триггер предложения о сотрудничестве
        if needs_partnership_proposal(message_text):
            session.intent = "partnership"
            session.lead_data = {"partnership_step": "details"}
            flag_modified(session, "lead_data")
            db.commit()
            
            await update.message.reply_text(
                "🤝 Здорово, что вы хотите сотрудничать с нами!\n\n"
                "📝 Расскажите, пожалуйста, подробнее о вашем предложении — в чём его суть?"
            )
            return
        # ============ КОНЕЦ ОБРАБОТКИ ПРЕДЛОЖЕНИЙ ============
        
        # ============ КОРОТКИЙ ФЛОУ: ДОПОЛНИТЕЛЬНОЕ БРОНИРОВАНИЕ ============
        if session.intent == "birthday_additional":
            lead_data = session.lead_data or {}
            
            # Шаг 1: Собираем дату
            if not lead_data.get("event_date"):
                extracted = agent.extract_lead_data(message_text, {})
                if extracted.get("event_date"):
                    lead_data["event_date"] = extracted["event_date"]
                    session.lead_data = lead_data
                    flag_modified(session, "lead_data")
                    db.commit()
                    
                    await update.message.reply_text(
                        f"📅 Отлично, {extracted['event_date']}!\n\n"
                        "👶 Сколько детей будет на этом празднике, включая именинника?"
                    )
                    return
                else:
                    await update.message.reply_text(
                        "📅 Пожалуйста, укажите дату праздника (например: 15 февраля)"
                    )
                    return
            
            # Шаг 2: Собираем количество детей → сразу в CRM
            if not lead_data.get("kids_count"):
                extracted = agent.extract_lead_data(message_text, lead_data)
                if extracted.get("kids_count"):
                    # Берём данные из контекста
                    phone = context.user_data.get("additional_booking_phone")
                    name = context.user_data.get("additional_booking_name") or user.first_name
                    
                    if not phone:
                        # Пытаемся найти телефон в CRM
                        try:
                            contact = await amocrm_client.find_contact_by_telegram_id(user.id)
                            if contact:
                                contact_info = amocrm_client.get_contact_info(contact)
                                phone = contact_info.get("phone")
                        except Exception as e:
                            logger.error(f"Failed to get phone from CRM: {e}")
                    
                    if not phone:
                        # Телефона нет — просим указать
                        lead_data["kids_count"] = extracted["kids_count"]
                        lead_data["waiting_phone"] = True
                        session.lead_data = lead_data
                        flag_modified(session, "lead_data")
                        db.commit()
                        
                        await update.message.reply_text(
                            "📱 Укажите номер телефона для связи:"
                        )
                        return
                    
                    # Создаём новый лид и сразу отправляем в CRM
                    new_lead = force_create_new_lead(
                        user_id,
                        park_id="nn",
                        username=user.username,
                        source="telegram"
                    )
                    
                    # removed redundant import
                    update_lead_from_data(new_lead.id, {
                        "event_date": lead_data["event_date"],
                        "kids_count": extracted["kids_count"],
                        "phone": phone,
                        "customer_name": name
                    })
                    
                    # Отправляем в AmoCRM
                    try:
                        lead_dict = {
                            "event_date": lead_data["event_date"],
                            "kids_count": extracted["kids_count"],
                            "phone": phone,
                            "customer_name": name,
                            "source": "telegram"
                        }
                        amocrm_deal_id, amocrm_contact_id = await send_lead_to_amocrm(
                            lead_dict,
                            telegram_id=user_id,
                            username=user.username
                        )
                        if amocrm_deal_id:
                            save_amocrm_deal_id(new_lead.id, str(amocrm_deal_id))
                            if amocrm_contact_id:
                                save_amocrm_contact_id(new_lead.id, str(amocrm_contact_id))
                            logger.info(f"Additional booking Lead #{new_lead.id} created in AmoCRM, deal_id={amocrm_deal_id}")
                        
                        # Уведомляем менеджеров
                        msg_text = format_lead_message("telegram", user_id, lead_dict, username=user.username)
                        await send_to_birthday_channel(msg_text)
                        mark_lead_sent_to_manager(new_lead.id)
                    except Exception as e:
                        logger.error(f"Failed to send additional booking to AmoCRM: {e}")
                    
                    # Сбрасываем intent
                    session.intent = "unknown"
                    session.lead_data = {}
                    db.commit()
                    
                    # Очищаем context
                    context.user_data.pop("additional_booking_phone", None)
                    context.user_data.pop("additional_booking_name", None)
                    
                    await update.message.reply_text(
                        f"✅ Дополнительное бронирование создано!\n\n"
                        f"📅 Дата: {lead_data['event_date']}\n"
                        f"👶 Детей: {extracted['kids_count']}\n\n"
                        "Феи праздников свяжутся с вами для уточнения деталей! 🧚‍♀️💚"
                    )
                    return
                else:
                    await update.message.reply_text(
                        "👶 Пожалуйста, укажите количество детей (например: 8 детей)"
                    )
                    return
            
            # Шаг 3: Если ждём телефон
            if lead_data.get("waiting_phone"):
                # Проверяем, похоже ли на телефон
                phone_pattern = r'[\d\+\(\)\-\s]{7,}'
                if re.search(phone_pattern, message_text):
                    phone = message_text.strip()
                    name = context.user_data.get("additional_booking_name") or user.first_name
                    
                    # Создаём лид и отправляем в CRM
                    new_lead = force_create_new_lead(
                        user_id,
                        park_id="nn",
                        username=user.username,
                        source="telegram"
                    )
                    
                    # removed redundant import
                    update_lead_from_data(new_lead.id, {
                        "event_date": lead_data["event_date"],
                        "kids_count": lead_data["kids_count"],
                        "phone": phone,
                        "customer_name": name
                    })
                    
                    # Отправляем в AmoCRM
                    try:
                        lead_dict = {
                            "event_date": lead_data["event_date"],
                            "kids_count": lead_data["kids_count"],
                            "phone": phone,
                            "customer_name": name,
                            "source": "telegram"
                        }
                        amocrm_deal_id, amocrm_contact_id = await send_lead_to_amocrm(
                            lead_dict,
                            telegram_id=user_id,
                            username=user.username
                        )
                        if amocrm_deal_id:
                            save_amocrm_deal_id(new_lead.id, str(amocrm_deal_id))
                            if amocrm_contact_id:
                                save_amocrm_contact_id(new_lead.id, str(amocrm_contact_id))
                        
                        msg_text = format_lead_message("telegram", user_id, lead_dict, username=user.username)
                        await send_to_birthday_channel(msg_text)
                        mark_lead_sent_to_manager(new_lead.id)
                    except Exception as e:
                        logger.error(f"Failed to send additional booking to AmoCRM: {e}")
                    
                    # Сбрасываем
                    session.intent = "unknown"
                    session.lead_data = {}
                    db.commit()
                    
                    await update.message.reply_text(
                        f"✅ Дополнительное бронирование создано!\n\n"
                        f"📅 Дата: {lead_data['event_date']}\n"
                        f"👶 Детей: {lead_data['kids_count']}\n\n"
                        "Феи праздников свяжутся с вами! 🧚‍♀️💚"
                    )
                    return
                else:
                    await update.message.reply_text(
                        "📱 Пожалуйста, укажите корректный номер телефона:"
                    )
                    return
        # ============ КОНЕЦ КОРОТКОГО ФЛОУ ============
        
        # Определяем intent

        current_intent = session.intent
        intent_result = detect_intent(message_text)
        
        # Логика переключения intent
        if current_intent == "unknown":
            # Первое определение
            session.intent = intent_result.intent
            db.commit()
            logger.info(f"Intent detected: {intent_result.intent} ({intent_result.confidence})")
        elif current_intent == "general" and intent_result.intent == "birthday" and intent_result.confidence >= 0.7:
            # Переключаемся с general на birthday при явных триггерах
            session.intent = "birthday"
            session.lead_data = {}  # Сбрасываем данные лида
            db.commit()
            logger.info(f"Intent switched: general -> birthday")
        elif current_intent == "general" and intent_result.intent == "events" and intent_result.confidence >= 0.7:
            # Переключаемся с general на events при вопросах об афише
            session.intent = "events"
            db.commit()
            logger.info(f"Intent switched: general -> events")
        elif current_intent == "birthday" and intent_result.intent == "events" and intent_result.confidence >= 0.8:
            # С birthday на events только при очень явных триггерах
            session.intent = "events"
            db.commit()
            logger.info(f"Intent switched: birthday -> events")
        
        # Получаем историю сообщений
        history = []
        for msg in db.query(Message).filter(Message.session_id == session.id).order_by(Message.id.desc()).limit(10).all():
            history.insert(0, {"role": msg.role, "content": msg.content})
        
        # Получаем контекст из RAG
        rag_context = rag.get_context(message_text, session.intent)
        
        # Для birthday ветки — сохраняем данные в Lead (надёжно в БД)
        current_lead = None
        lead_data = {}
        
        if session.intent == "birthday":
            # Получаем или создаём Lead в БД
            current_lead = get_or_create_lead(
                user_id, 
                source="telegram", 
                park_id="nn", 
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name
            )
            
            # Проверяем: есть ли у юзера активная заявка с датой И упоминает ли он новую дату
            active_lead_info = get_active_lead_info(user_id)
            if active_lead_info and active_lead_info.get("event_date"):
                # Проверяем, содержит ли сообщение дату (паттерн: число + месяц)
                date_pattern = r'\b\d{1,2}\s*(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря|янв|фев|мар|апр|июн|июл|авг|сен|окт|ноя|дек)\b'
                has_new_date = bool(re.search(date_pattern, message_text.lower()))
                
                # Также проверяем не относится ли это к изменению (ключевые слова)
                is_modification = any(x in message_text.lower() for x in ["изменить", "поменять", "перенести", "другую дату", "сменить"])
                
                # Если есть новая дата и нет явного указания на изменение — спрашиваем
                if has_new_date and not is_modification:
                    keyboard = [
                        [InlineKeyboardButton("🔄 Изменить текущую заявку", callback_data="booking_modify")],
                        [InlineKeyboardButton("➕ Создать новое бронирование", callback_data="booking_new")]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    existing_date = active_lead_info["event_date"]
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=f"Вижу, у вас уже есть заявка на {existing_date} 📅\n\nВы хотите изменить эту заявку или создать новое бронирование?",
                        reply_markup=reply_markup
                    )
                    
                    # Сохраняем новую дату в контексте для последующего использования
                    context.user_data["pending_new_date"] = message_text
                    return  # Ждём выбора пользователя
            
            # Извлекаем данные из ВСЕЙ истории переписки (не только последнего сообщения)
            # Это критически важно т.к. имя, телефон, дата могут быть в разных сообщениях
            current_lead_data = lead_to_dict(current_lead)
            
            # Собираем все сообщения пользователя из истории
            user_messages = [msg["content"] for msg in history if msg["role"] == "user"][-10:]
            full_conversation = "\n".join(user_messages)
            
            extracted = agent.extract_lead_data(full_conversation, current_lead_data)
            
            # Обновляем Lead в БД
            if extracted:
                # Если имя не указано — берём из профиля
                if not extracted.get("customer_name") and user.first_name:
                    extracted["customer_name"] = user.first_name
                
                current_lead = update_lead_from_data(current_lead.id, extracted)
                logger.info(f"Lead #{current_lead.id} updated with: {extracted}")
            
            # Перечитываем lead из БД чтобы получить актуальные данные (включая телефон)
            current_lead = db.query(Lead).filter(Lead.id == current_lead.id).first()
            
            # Проверяем: нужно подтвердить телефон для нового бронирования?
            pending_phone = context.user_data.get("pending_phone_confirm")
            lead_data = lead_to_dict(current_lead)
            
            # Если есть pending телефон И только что получили kids_count — спрашиваем
            if pending_phone and extracted and extracted.get("kids_count") and not lead_data.get("phone"):
                keyboard = [
                    [InlineKeyboardButton(f"✅ Да, использовать {pending_phone}", callback_data="confirm_phone_yes")],
                    [InlineKeyboardButton("📱 Указать другой номер", callback_data="confirm_phone_no")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"📱 Использовать этот номер телефона для бронирования?\n\n{pending_phone}",
                    reply_markup=reply_markup
                )
                return  # Ждём выбора
            
            # РАННЯЯ ОТПРАВКА В CRM: Как только есть телефон — создаём сделку
            
            # Проверяем валидность телефона (минимум 10 цифр)
            phone = lead_data.get("phone", "")
            phone_digits = ''.join(filter(str.isdigit, str(phone))) if phone else ""
            has_valid_phone = len(phone_digits) >= 10
            
            logger.info(f"Early CRM check: phone='{phone}', has_valid_phone={has_valid_phone}, amocrm_deal_id={current_lead.amocrm_deal_id}")
            
            if has_valid_phone and not current_lead.amocrm_deal_id:
                # Телефон есть, сделки ещё нет — СОЗДАЁМ!
                logger.info(f"Phone received! Creating AmoCRM deal for Lead #{current_lead.id}")
                try:
                    lead_dict = lead_data.copy()
                    lead_dict["source"] = "telegram"
                    lead_dict["first_name"] = user.first_name  # Для имени из профиля
                    
                    amocrm_deal_id, amocrm_contact_id = await send_lead_to_amocrm(
                        lead_dict, 
                        telegram_id=user_id,
                        username=user.username
                    )
                    if amocrm_deal_id:
                        # Сохраняем ID сделки и контакта через lead_service (правильная сессия БД)
                        save_amocrm_deal_id(current_lead.id, str(amocrm_deal_id))
                        if amocrm_contact_id:
                            save_amocrm_contact_id(current_lead.id, str(amocrm_contact_id))
                            current_lead.amocrm_contact_id = str(amocrm_contact_id)
                        current_lead.amocrm_deal_id = str(amocrm_deal_id)  # Обновляем локальный объект
                        logger.info(f"Lead #{current_lead.id} created in AmoCRM, deal_id={amocrm_deal_id}, contact_id={amocrm_contact_id}")
                        
                        # Отправляем уведомление менеджерам
                        msg_text = format_lead_message("telegram", user_id, lead_data, username=user.username)
                        await send_to_birthday_channel(msg_text)
                        mark_lead_sent_to_manager(current_lead.id)
                        logger.info(f"Manager notification sent for Lead #{current_lead.id}!")
                except Exception as e:
                    logger.error(f"Failed to send to AmoCRM: {e}")
            
            elif has_valid_phone and current_lead.amocrm_deal_id:
                # Телефон есть, сделка уже есть — ОБНОВЛЯЕМ!
                try:
                    await amocrm_client.update_deal_fields(
                        int(current_lead.amocrm_deal_id), 
                        lead_data
                    )
                except Exception as e:
                    logger.error(f"Failed to update AmoCRM deal: {e}")
            
            # Формируем lead_data для передачи в agent (добавляем first_name для имени из профиля)
            lead_data["first_name"] = user.first_name
        
        # Проверяем статус сделки в AmoCRM
        deal_in_work = False
        status_just_changed = False
        
        if current_lead and current_lead.amocrm_deal_id:
            try:
                deal_in_work = await amocrm_client.is_deal_in_work(int(current_lead.amocrm_deal_id))
                
                # Если статус изменился и клиент ещё не уведомлён
                if deal_in_work and not current_lead.status_notified:
                    status_just_changed = True
                    mark_status_notified(current_lead.id)
                    logger.info(f"Lead #{current_lead.id} status changed to 'in work', notifying client")
            except Exception as e:
                logger.error(f"Failed to check deal status: {e}")
        
        # Показываем индикатор "печатает..."
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        
        # Если статус только что изменился — сначала уведомляем
        if status_just_changed:
            await context.bot.send_message(
                chat_id=update.effective_chat.id, 
                text="🎉 Отличные новости! Феи праздников уже начали работу над вашим мероприятием! 🧚‍♀️✨"
            )
        
        # Генерируем ответ (передаём флаг deal_in_work)
        response = agent.generate_response(
            message=message_text,
            intent=session.intent,
            history=history,
            rag_context=rag_context,
            lead_data=lead_data,
            deal_in_work=deal_in_work
        )
        
        # Сохраняем ответ
        assistant_message = Message(session_id=session.id, role="assistant", content=response)
        db.add(assistant_message)
        db.commit()
        
        # КРИТИЧНО: Извлекаем данные из ОТВЕТА бота и сохраняем сразу
        # Бот часто суммаризирует данные в своем ответе (например: "Спасибо, Наталья!")
        # НО НЕ извлекаем extras из ответа бота — иначе слова типа "аниматоры, торты" попадут в заказ!
        if current_lead and session.intent == "birthday":
            response_data = agent.extract_lead_data(response, lead_data)
            if response_data:
                # Удаляем extras из response_data — они должны извлекаться ТОЛЬКО из сообщений пользователя
                response_data.pop("extras", None)
                if response_data:  # Если остались какие-то данные
                    current_lead = update_lead_from_data(current_lead.id, response_data)
                    logger.info(f"Lead #{current_lead.id} updated from bot response: {response_data}")
                # Обновляем lead_data для следующих итераций
                lead_data = lead_to_dict(current_lead)
                
                # Синхронизируем с AmoCRM если сделка уже создана
                if current_lead.amocrm_deal_id:
                    try:
                        await amocrm_client.update_deal_fields(
                            int(current_lead.amocrm_deal_id), 
                            lead_data
                        )
                        
                        # Обновляем имя контакта если клиент указал другое имя
                        if current_lead.amocrm_contact_id and lead_data.get("customer_name"):
                            await amocrm_client.update_contact_name(
                                int(current_lead.amocrm_contact_id),
                                lead_data["customer_name"]
                            )
                        
                        # Обновляем переписку в AmoCRM (добавляем новую заметку)
                        conversation_lines = []
                        for msg in history[-20:]:
                            role_emoji = "👤" if msg["role"] == "user" else "🤖"
                            conversation_lines.append(f"{role_emoji} {msg['content'][:300]}")
                        conversation = "\n\n".join(conversation_lines)
                        await amocrm_client.add_note(
                            int(current_lead.amocrm_deal_id), 
                            f"📱 Обновление переписки:\n\n{conversation}"
                        )
                        
                        logger.info(f"AmoCRM deal {current_lead.amocrm_deal_id} synced with new data")
                    except Exception as e:
                        logger.error(f"Failed to sync AmoCRM deal: {e}")
        
        # Отправляем ответ пользователю (ВСЕГДА)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=response)
        
        # Если бот сказал про "передам менеджеру" — создаём задачу в AmoCRM и уведомляем менеджеров
        if deal_in_work and current_lead and current_lead.amocrm_deal_id:
            is_change_request = any(x in response.lower() for x in ["передам менеджеру", "перезвонят", "передал вашу просьбу"])
            if is_change_request:
                try:
                    # Создаём задачу в AmoCRM
                    task_text = f"⚠️ Клиент просит изменения: {message_text[:200]}"
                    await amocrm_client.create_task(int(current_lead.amocrm_deal_id), task_text)
                    
                    # Добавляем заметку в сделку
                    await amocrm_client.add_note(
                        int(current_lead.amocrm_deal_id),
                        f"⚠️ КЛИЕНТ ПРОСИТ ВНЕСТИ ИЗМЕНЕНИЯ:\n\n{message_text}"
                    )
                    
                    # Отправляем уведомление менеджерам в TG
                    customer_name = lead_data.get("customer_name") or "Клиент"
                    manager_msg = f"⚠️ *ЗАПРОС НА ИЗМЕНЕНИЕ*\n\n"
                    manager_msg += f"👤 {customer_name}\n"
                    manager_msg += f"📱 {lead_data.get('phone', 'нет телефона')}\n\n"
                    manager_msg += f"💬 Просьба клиента:\n{message_text[:300]}\n\n"
                    manager_msg += f"🔗 Сделка #{current_lead.amocrm_deal_id}"
                    await send_to_managers(manager_msg)
                    
                    logger.info(f"Created callback task for Lead #{current_lead.id}")
                except Exception as e:
                    logger.error(f"Failed to create callback task: {e}")
            
            # Проверяем, подтвердил ли бот добавление доп.услуги (extras)
            # Ищем в ответе бота подтверждение: "оформить?", "добавить?", "заявку передал"
            extras_confirmation_keywords = [
                "заявк", "передал", "оформи", "добав", "записал", 
                "менеджер свяжется", "перезвонят", "уведомил"
            ]
            if any(kw in response.lower() for kw in extras_confirmation_keywords):
                # Определяем тип услуги по сообщению клиента
                extras_type = get_extras_type(message_text)
                if extras_type:
                    try:
                        # Отправляем уведомление в TG-канал
                        user_name = lead_data.get("customer_name") or user.first_name or "Клиент"
                        msg = format_extras_request_message(
                            platform="telegram",
                            user_id=user_id,
                            user_name=user_name,
                            extras_type=extras_type,
                            deal_id=current_lead.amocrm_deal_id,
                            phone=lead_data.get("phone"),
                            username=user.username,
                            context=message_text
                        )
                        await send_to_managers(msg)
                        
                        # Добавляем примечание в AmoCRM
                        from core.notifications import EXTRAS_TYPES
                        extras_info = EXTRAS_TYPES.get(extras_type, {"name": extras_type, "emoji": "✨"})
                        note_text = f"{extras_info['emoji']} КЛИЕНТ ХОЧЕТ ДОБАВИТЬ: {extras_info['name']}\n\n💬 Контекст: {message_text[:300]}"
                        await amocrm_client.add_note(
                            int(current_lead.amocrm_deal_id),
                            note_text
                        )
                        
                        # Создаём задачу на перезвон
                        task_text = f"📞 Перезвонить: клиент хочет добавить {extras_info['name']}"
                        await amocrm_client.create_task(int(current_lead.amocrm_deal_id), task_text)
                        
                        logger.info(f"Created extras request notification for Lead #{current_lead.id}: {extras_type}")
                    except Exception as e:
                        logger.error(f"Failed to create extras notification: {e}")
        
        # Проверяем, бот сообщил что заявка принята (для отправки фото)
        is_confirmation = current_lead and \
            any(x in response.lower() for x in ["передана феям", "заявка принята", "передал заявку"])
        
        if is_confirmation:
            # "Заявка принята" — отправляем картинку для красоты
            logger.info(f"Bot announced confirmation for Lead #{current_lead.id} — sending photo!")
            
            # НЕ парсим ответ бота! Данные уже собраны ранее.
            # Это исправляет баг, когда extras извлекались из текста бота
            # ("аниматоры, торты, шары" упоминались как предложение, а не запрос клиента)
            
            # Подсказка про /booking для discoverability
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="💡 *Подсказка:* чтобы проверить статус бронирования в любой момент — напишите /booking",
                parse_mode="Markdown"
            )

        
        # Если intent всё ещё unknown — показываем кнопки
        if session.intent == "unknown":
            keyboard = [
                [InlineKeyboardButton("🎟 Узнать о парке", callback_data="intent_general")],
                [InlineKeyboardButton("🎉 Организовать праздник", callback_data="intent_birthday")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "Или выберите, что вас интересует:",
                reply_markup=reply_markup
            )
            
    except Exception as e:
        # Fallback logging to stdout
        print(f"CRITICAL EXCEPTION IN HANDLER: {e}", flush=True)
        import traceback
        traceback.print_exc()
        
        try:
            with open("/root/jungle_bot/crash.log", "a") as f:
                f.write(f"\nCRASH at {datetime.now()}:\n")
                traceback.print_exc(file=f)
        except Exception as log_result:
            print(f"FAILED TO WRITE LOG: {log_result}", flush=True)

        logger.error(f"Error handling message: {e}")
        await update.message.reply_text(
            "Ой, ошибка (код 505) 😅\n"
            "Попробуйте ещё раз или позвоните нам: +7 (831) 213-50-50"
        )
    finally:
        db.close()


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
