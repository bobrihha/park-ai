"""
API для веб-чата на сайте.
Использует FastAPI для обработки сообщений.
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any
import logging
import uuid
import os
import httpx
from datetime import datetime
import re

from core.agent import Agent
from core.rag import RAGSystem
from core.intent_router import detect_intent
from core.amocrm import amocrm_client
from core.messages import BIRTHDAY_WELCOME_MESSAGE
from core.utils import (
    parse_user_date,
    format_date_ru,
    build_birthday_date_question,
    parse_kids_count,
    filter_extras_from_message,
    should_defer_phone_request,
    extract_phone_from_message,
    extract_format_from_message,
    build_format_choice_message,
    parse_time_from_message,
)
from db.database import SessionLocal
from db.models import Session as DBSession, Message, Lead
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import inspect
from core.lead_service import (
    get_or_create_lead,
    update_lead_from_data,
    mark_lead_sent_to_manager,
    lead_to_dict,
    force_create_new_lead,
)
from core.notifications import (
    send_to_managers,
    send_to_birthday_channel,
    format_lead_message,
    needs_human_escalation,
    needs_complaint_flow,
    format_complaint_message,
    needs_lost_item_flow,
    format_lost_item_message,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _refresh_lead(db, lead: Lead | None) -> Lead | None:
    """Reload lead in текущей DB-сессии to avoid detached instances."""
    if not lead:
        return None
    lead_id = None
    try:
        state = inspect(lead)
        if state.identity:
            lead_id = state.identity[0]
    except Exception:
        lead_id = None
    if not lead_id:
        # Fallback without triggering lazy load
        lead_id = lead.__dict__.get("id")
    if not lead_id:
        return None
    return db.query(Lead).filter(Lead.id == lead_id).first()

app = FastAPI(
    title="Jungle City Chat API",
    description="API для чат-виджета на сайте nn.jucity.ru",
    version="1.0.0"
)


# CORS - разрешаем доступ с сайта
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://nn.jucity.ru",
        "https://www.nn.jucity.ru",
        "http://localhost:3000",  # для тестирования
        "http://localhost:8080",
        "*"  # временно для тестирования
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Статические файлы (чат-виджет)
import os
from fastapi.staticfiles import StaticFiles
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Инициализация компонентов
agent = Agent()
rag = RAGSystem()


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    user_name: Optional[str] = None
    user_phone: Optional[str] = None


class ButtonData(BaseModel):
    text: str
    callback: str


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    buttons: Optional[list] = None  # Список кнопок для UI


class CallbackRequest(BaseModel):
    session_id: str
    callback: str  # Например: "format_room", "time_10_30"


class RegisterRequest(BaseModel):
    session_id: str
    name: str
    phone: str


@app.get("/")
async def root():
    return {"status": "ok", "service": "Jungle City Chat API"}


@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


@app.post("/chat/register")
async def register_user(request: RegisterRequest):
    """
    Регистрация пользователя из веб-чата.
    Просто сохраняем данные для идентификации.
    Сделка в AmoCRM создаётся только при оформлении заявки на ДР.
    """
    try:
        db = SessionLocal()
        
        # Ищем или создаём сессию
        session = db.query(DBSession).filter(
            DBSession.telegram_id == request.session_id
        ).first()
        
        if not session:
            session = DBSession(
                telegram_id=request.session_id,
                park_id="nn",
                intent="unknown",
                lead_data={}
            )
            db.add(session)
            db.commit()
        
        # Сохраняем данные пользователя в сессии
        session.lead_data = session.lead_data or {}
        session.lead_data["customer_name"] = request.name
        session.lead_data["phone"] = request.phone
        session.lead_data["web_registered"] = True
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(session, "lead_data")
        db.commit()
        
        logger.info(f"Web user registered: {request.name}, {request.phone}, session={request.session_id}")
        
        db.close()
        
        return {"status": "ok"}
        
    except Exception as e:
        logger.error(f"Registration error: {e}")
        return {"status": "error", "message": str(e)}


@app.post("/chat/callback", response_model=ChatResponse)
async def chat_callback(request: CallbackRequest):
    """
    Обработка нажатий на кнопки в веб-чате.
    Возвращает следующее сообщение и, возможно, новые кнопки.
    """
    try:
        db = SessionLocal()
        session_id = request.session_id
        callback = request.callback
        
        # Ищем сессию
        session = db.query(DBSession).filter(
            DBSession.telegram_id == session_id
        ).first()
        
        if not session:
            db.close()
            return ChatResponse(
                reply="Сессия не найдена. Напишите любое сообщение чтобы начать.",
                session_id=session_id,
                buttons=None
            )
        
        session.lead_data = session.lead_data or {}
        reply_text = ""
        buttons = None
        
        if callback == "format_room":
            # Выбрали тематическую комнату
            session.lead_data["format"] = "Тематическая комната"
            flag_modified(session, "lead_data")
            db.commit()
            
            # Обновляем лид если есть
            lead = db.query(Lead).filter(
                Lead.telegram_id == session_id,
                Lead.park_id == "nn",
                Lead.status.in_(["new", "contacted"])
            ).first()
            if lead:
                update_lead_from_data(lead.id, {"format": "Тематическая комната"})
            
            reply_text = "🏠 Отлично, тематическая комната!\n\nВыберите удобное время начала:"
            buttons = [
                {"text": "🕥 10:30", "callback": "time_10_30"},
                {"text": "🕝 14:30", "callback": "time_14_30"},
                {"text": "🕡 18:30", "callback": "time_18_30"}
            ]
            
        elif callback == "format_zone":
            # Выбрали ресторан
            session.lead_data["format"] = "Ресторан"
            flag_modified(session, "lead_data")
            db.commit()
            
            # Обновляем лид если есть
            lead = db.query(Lead).filter(
                Lead.telegram_id == session_id,
                Lead.park_id == "nn",
                Lead.status.in_(["new", "contacted"])
            ).first()
            if lead:
                update_lead_from_data(lead.id, {"format": "Ресторан"})
                mark_lead_sent_to_manager(lead.id)
                
                # Создаём/обновляем сделку в AmoCRM
                try:
                    lead_data = lead_to_dict(lead)
                    lead_data["source"] = "web"
                    result = await send_lead_to_amocrm(lead_data)
                    if result and result[0]:
                        save_amocrm_deal_id(lead.id, str(result[0]))
                except Exception as e:
                    logger.error(f"AmoCRM error: {e}")
                
                # Уведомляем менеджеров
                try:
                    msg_text = format_lead_message("web", session_id, lead_to_dict(lead))
                    await send_to_birthday_channel(msg_text)
                except Exception as e:
                    logger.error(f"Notify error: {e}")
            
            # Показываем сообщение с кнопками допуслуг (как на скриншоте)
            reply_text = (
                "🍰 Записал столик в ресторане!\n\n"
                "Менеджер из отдела праздников скоро свяжется с вами 🧚\n\n"
                "А пока вы ждёте — у нас есть своя кондитерская! 🎂\n"
                "Можете выбрать торт к празднику или посмотреть другие услуги:"
            )
            buttons = [
                {"text": "🎂 Посмотреть торты", "callback": "view_cakes"},
                {"text": "🎭 Аниматоры и шоу", "callback": "view_animators"},
                {"text": "🎁 Пакеты под ключ", "callback": "view_packages"}
            ]


            
        elif callback in ("time_10_30", "time_14_30", "time_18_30"):
            # Выбрали время
            time_map = {
                "time_10_30": "10:30",
                "time_14_30": "14:30",
                "time_18_30": "18:30"
            }
            selected_time = time_map[callback]
            
            session.lead_data["time"] = selected_time
            flag_modified(session, "lead_data")
            db.commit()
            
            # Обновляем лид если есть
            lead = db.query(Lead).filter(
                Lead.telegram_id == session_id,
                Lead.park_id == "nn",
                Lead.status.in_(["new", "contacted"])
            ).first()
            if lead:
                update_lead_from_data(lead.id, {"time": selected_time})
                mark_lead_sent_to_manager(lead.id)
                
                # Создаём/обновляем сделку в AmoCRM
                try:
                    lead_data = lead_to_dict(lead)
                    lead_data["source"] = "web"
                    result = await send_lead_to_amocrm(lead_data)
                    if result and result[0]:
                        save_amocrm_deal_id(lead.id, str(result[0]))
                except Exception as e:
                    logger.error(f"AmoCRM error: {e}")
                
                # Уведомляем менеджеров
                try:
                    msg_text = format_lead_message("web", session_id, lead_to_dict(lead))
                    await send_to_birthday_channel(msg_text)
                except Exception as e:
                    logger.error(f"Notify error: {e}")
            
            # Показываем сообщение с кнопками допуслуг (как на скриншоте)
            reply_text = (
                f"⏰ Записал на {selected_time}!\n\n"
                f"Менеджер из отдела праздников скоро свяжется с вами 🧚\n\n"
                f"А пока вы ждёте — у нас есть своя кондитерская! 🎂\n"
                f"Можете выбрать торт к празднику или посмотреть другие услуги:"
            )
            buttons = [
                {"text": "🎂 Посмотреть торты", "callback": "view_cakes"},
                {"text": "🎭 Аниматоры и шоу", "callback": "view_animators"},
                {"text": "🎁 Пакеты под ключ", "callback": "view_packages"}
            ]

        elif callback == "view_cakes":
            reply_text = (
                "🎂 Наша кондитерская!\n\n"
                "У нас есть торты на любой вкус — от классических до тематических с персонажами!\n\n"
                "📱 Посмотреть каталог: https://catalog.botcicada.ru/menu.html\n\n"
                "Также можно принести свой торт (сбор 1000₽ за вынос торта).\n\n"
                "Если нужна помощь с выбором — пишите, подскажу! 😊"
            )

        elif callback == "view_animators":
            reply_text = (
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

        elif callback == "view_packages":
            reply_text = (
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

        
        else:
            reply_text = "Неизвестная команда. Напишите, чем могу помочь?"
        
        db.close()
        
        return ChatResponse(
            reply=reply_text,
            session_id=session_id,
            buttons=buttons
        )
        
    except Exception as e:
        logger.error(f"Callback error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Основной endpoint для чата.
    Принимает сообщение и session_id, возвращает ответ бота.
    """
    try:
        db = SessionLocal()
        
        # Генерируем или используем существующий session_id
        session_id = request.session_id or f"web_{uuid.uuid4().hex[:12]}"
        
        # Ищем или создаём сессию
        session = db.query(DBSession).filter(
            DBSession.telegram_id == session_id
        ).first()
        
        if not session:
            session = DBSession(
                telegram_id=session_id,
                park_id="nn",
                intent="unknown"
            )
            db.add(session)
            db.commit()
            db.refresh(session)
            logger.info(f"Created new web session: {session_id}")
        
        # Сохраняем сообщение пользователя
        user_message = Message(
            session_id=session.id,
            role="user",
            content=request.message
        )
        db.add(user_message)
        db.commit()
        
        # Определяем intent — проверяем birthday всегда (не только если unknown)
        intent_just_switched_to_birthday = False
        previous_intent = session.intent
        
        detected_result = detect_intent(request.message)
        detected = detected_result.intent if hasattr(detected_result, 'intent') else str(detected_result)
        
        # Если обнаружен birthday и ранее был не birthday — переключаемся
        if detected == "birthday" and previous_intent != "birthday":
            session.intent = "birthday"
            db.commit()
            intent_just_switched_to_birthday = True
            logger.info(f"Intent switched to birthday from {previous_intent}")
        # Если intent был unknown и обнаружен любой другой — устанавливаем
        elif previous_intent == "unknown" and detected != "unknown":
            session.intent = detected
            db.commit()
            logger.info(f"Detected intent: {detected}")
        
        # Ранний флаг: пользователь явно хочет начать новую бронь
        start_new_booking = False
        text_lower = request.message.lower().strip()
        change_keywords = ["изменить", "перенести", "поменять", "другую дату", "сменить"]
        start_keywords = ["хочу организовать", "хочу забронировать", "забронировать праздник", "организовать день рождения", "хочу праздник"]
        start_new_booking = any(k in text_lower for k in start_keywords) and not any(k in text_lower for k in change_keywords)

        # Если нужно начать новую бронь — переключаем intent и создаём новый лид сразу
        current_lead = None
        lead_data = {}
        fresh_lead = False
        if start_new_booking:
            if session.intent != "birthday":
                session.intent = "birthday"
                db.commit()
            current_lead = force_create_new_lead(session_id, park_id="nn", source="web")
            current_lead = _refresh_lead(db, current_lead)
            lead_data = lead_to_dict(current_lead) if current_lead else {}
            # Сбросим служебные флаги, сохраняя данные регистрации
            preserved = {}
            if session.lead_data and session.lead_data.get("web_registered"):
                preserved = {
                    "web_registered": True,
                    "customer_name": session.lead_data.get("customer_name"),
                    "phone": session.lead_data.get("phone"),
                }
            session.lead_data = preserved
            flag_modified(session, "lead_data")
            db.commit()
            fresh_lead = True

        # Получаем историю сообщений
        history = db.query(Message).filter(
            Message.session_id == session.id
        ).order_by(Message.id).all()
        
        history_list = [{"role": m.role, "content": m.content} for m in history]
        
        # Получаем RAG контекст
        rag_context = rag.get_context(request.message, session.intent)

        # Предварительный разбор даты (нужен для приветствия и шагов сценария)
        parsed_date_in_message = None
        if session.intent == "birthday" or intent_just_switched_to_birthday or start_new_booking:
            parsed_date_in_message = parse_user_date(request.message)
        
        # Для birthday — работаем с Lead
        if session.intent == "birthday":
            if not current_lead:
                current_lead = get_or_create_lead(session_id, source="web", park_id="nn")
                current_lead = _refresh_lead(db, current_lead)
            
            # Используем данные из регистрации (если есть)
            if session.lead_data and session.lead_data.get("web_registered"):
                if not current_lead.customer_name and session.lead_data.get("customer_name"):
                    current_lead = update_lead_from_data(current_lead.id, {
                        "customer_name": session.lead_data.get("customer_name")
                    })
                    current_lead = _refresh_lead(db, current_lead)
                if not current_lead.phone and session.lead_data.get("phone"):
                    current_lead = update_lead_from_data(current_lead.id, {
                        "phone": session.lead_data.get("phone")
                    })
                    current_lead = _refresh_lead(db, current_lead)
            
            # Также используем данные из запроса (если пользователь указал новые)
            if request.user_name and not current_lead.customer_name:
                current_lead = update_lead_from_data(current_lead.id, {"customer_name": request.user_name})
                current_lead = _refresh_lead(db, current_lead)
            if request.user_phone and not current_lead.phone:
                current_lead = update_lead_from_data(current_lead.id, {"phone": request.user_phone})
                current_lead = _refresh_lead(db, current_lead)
            
            # Извлекаем данные из сообщения (но не трогаем extras при старте новой брони)
            extracted = agent.extract_lead_data(request.message, {})
            if extracted and "extras" in extracted:
                last_bot_message = ""
                for msg in reversed(history):
                    if msg.role == "assistant":
                        last_bot_message = msg.content
                        break
                filtered_extras = filter_extras_from_message(
                    request.message,
                    extracted.get("extras"),
                    last_bot_message=last_bot_message
                )
                if filtered_extras:
                    extracted["extras"] = filtered_extras
                else:
                    extracted.pop("extras", None)
            # Если пользователь прислал время — фиксируем его и не даём LLM переписать дату
            time_candidate = parse_time_from_message(request.message)
            if time_candidate:
                extracted = extracted or {}
                extracted["time"] = time_candidate
                extracted.pop("event_date", None)
            if extracted:
                current_lead = update_lead_from_data(current_lead.id, extracted)
                current_lead = _refresh_lead(db, current_lead)
            lead_data = lead_to_dict(current_lead)
        
        # ============ ЖАЛОБЫ — обработка жалоб на обслуживание ============
        if needs_complaint_flow(request.message):
            # Получаем данные пользователя из сессии
            user_name = session.lead_data.get("customer_name", "Не указано") if session.lead_data else "Не указано"
            user_phone = session.lead_data.get("phone", "Не указан") if session.lead_data else "Не указан"
            
            # Формируем историю чата
            chat_history_text = "\n".join([
                f"{'Клиент' if m.role == 'user' else 'Бот'}: {m.content}"
                for m in history
            ])
            
            # Отправляем уведомление менеджерам
            complaint_msg = format_complaint_message(
                platform="web",
                user_id=session_id,
                user_name=user_name,
                complaint_text=request.message,
                phone=user_phone if user_phone != "Не указан" else None
            )
            await send_to_managers(complaint_msg)
            logger.info(f"Complaint notification sent for web user {user_name}")
            
            # Если телефон уже есть
            if user_phone != "Не указан":
                response = (
                    "😔 Нам очень жаль, что у вас остались негативные впечатления.\n\n"
                    "Информация передана руководству парка. "
                    "Мы обязательно разберёмся в ситуации и свяжемся с вами "
                    "в ближайшее время для решения вопроса.\n\n"
                    "Приносим извинения за доставленные неудобства. 💚"
                )
            else:
                # Телефона нет — запрашиваем
                response = (
                    "😔 Нам очень жаль, что у вас остались негативные впечатления.\n\n"
                    "Мы обязательно разберёмся в ситуации!\n\n"
                    "📱 Пожалуйста, оставьте ваш номер телефона — "
                    "руководство парка свяжется с вами для решения вопроса."
                )
            
            # Сохраняем ответ и возвращаем
            bot_message = Message(session_id=session.id, role="assistant", content=response)
            db.add(bot_message)
            db.commit()
            db.close()
            
            return ChatResponse(reply=response, session_id=session_id)
        # ============ КОНЕЦ ЖАЛОБЫ ============
        
        # ============ ПОТЕРЯШКИ — обработка потерянных вещей ============
        if session.intent == "lost_item" or needs_lost_item_flow(request.message):
            # Переключаем intent
            if session.intent != "lost_item":
                session.intent = "lost_item"
                db.commit()
                logger.info(f"Intent switched to lost_item")
            
            # Инициализируем lost_step если нет
            if not session.lead_data:
                session.lead_data = {}
            
            lost_step = session.lead_data.get("lost_step", "")
            text_lower = request.message.lower()
            
            # Если пользователь хочет выйти из опроса
            exit_keywords = ["нет", "другой вопрос", "не потерял", "ошибка", "отмена", "всё хорошо"]
            if lost_step and any(kw in text_lower for kw in exit_keywords):
                session.intent = "unknown"
                session.lead_data = {}
                flag_modified(session, "lead_data")
                db.commit()
                
                response = "Понял! 😊 Тогда чем могу помочь? Спрашивайте о парке, ценах или празднике! 💚"
                bot_message = Message(session_id=session.id, role="assistant", content=response)
                db.add(bot_message)
                db.commit()
                db.close()
                return ChatResponse(reply=response, session_id=session_id)
            
            # Обработка по шагам
            if lost_step == "date":
                # Сохраняем дату
                session.lead_data["lost_date"] = request.message
                session.lead_data["lost_step"] = "location"
                flag_modified(session, "lead_data")
                db.commit()
                
                response = "📍 В каком месте парка (или рядом с каким аттракционом) вы потеряли вещь?"
                bot_message = Message(session_id=session.id, role="assistant", content=response)
                db.add(bot_message)
                db.commit()
                db.close()
                return ChatResponse(reply=response, session_id=session_id)
            
            elif lost_step == "location":
                # Сохраняем место
                session.lead_data["lost_location"] = request.message
                session.lead_data["lost_step"] = "description"
                flag_modified(session, "lead_data")
                db.commit()
                
                response = "🔍 Опишите, пожалуйста, что именно вы потеряли (внешний вид, особые приметы):"
                bot_message = Message(session_id=session.id, role="assistant", content=response)
                db.add(bot_message)
                db.commit()
                db.close()
                return ChatResponse(reply=response, session_id=session_id)
            
            elif lost_step == "description":
                # Сохраняем описание и завершаем
                session.lead_data["lost_description"] = request.message
                session.lead_data["lost_step"] = "done"
                flag_modified(session, "lead_data")
                db.commit()
                
                # Получаем данные пользователя
                user_name = session.lead_data.get("customer_name", "Не указано")
                user_phone = session.lead_data.get("phone", "Не указан")
                lost_date = session.lead_data.get("lost_date", "Не указано")
                lost_location = session.lead_data.get("lost_location", "Не указано")
                lost_description = request.message
                
                # Формируем и отправляем уведомление
                lost_msg = format_lost_item_message(
                    platform="web",
                    user_id=session_id,
                    user_name=user_name,
                    lost_date=lost_date,
                    lost_location=lost_location,
                    lost_description=lost_description,
                    phone=user_phone if user_phone != "Не указан" else None
                )
                await send_to_managers(lost_msg)
                logger.info(f"Lost item notification sent for web user {user_name}")
                
                # Сбрасываем intent
                session.intent = "unknown"
                session.lead_data["lost_step"] = ""
                flag_modified(session, "lead_data")
                db.commit()
                
                response = (
                    "✅ Спасибо за информацию!\n\n"
                    "Ваше сообщение передано в бюро находок. "
                    "Если вещь будет найдена — мы обязательно свяжемся с вами!\n\n"
                    "📞 Также можете позвонить нам: +7 (831) 213-50-50\n\n"
                    "Чем ещё могу помочь? 💚"
                )
                bot_message = Message(session_id=session.id, role="assistant", content=response)
                db.add(bot_message)
                db.commit()
                db.close()
                return ChatResponse(reply=response, session_id=session_id)
            
            # Первый шаг — спрашиваем дату
            session.lead_data["lost_step"] = "date"
            flag_modified(session, "lead_data")
            db.commit()
            
            response = (
                "Ой-ой, это неприятно! 😟 Но не переживайте — мы поможем!\n\n"
                "Давайте я передам информацию в бюро находок.\n\n"
                "📅 Когда примерно вы были в парке и могли потерять вещь?"
            )
            bot_message = Message(session_id=session.id, role="assistant", content=response)
            db.add(bot_message)
            db.commit()
            db.close()
            return ChatResponse(reply=response, session_id=session_id)
        # ============ КОНЕЦ ПОТЕРЯШКИ ============

        
        # ============ BIRTHDAY WELCOME — приветственное сообщение как в TG/VK ============
        if (intent_just_switched_to_birthday or start_new_booking) and not parsed_date_in_message:
            birthday_welcome = BIRTHDAY_WELCOME_MESSAGE
            
            # Сохраняем ответ
            bot_message = Message(session_id=session.id, role="assistant", content=birthday_welcome)
            db.add(bot_message)
            db.commit()
            db.close()
            
            logger.info(f"Sent birthday welcome message for web session {session_id}")
            return ChatResponse(reply=birthday_welcome, session_id=session_id)
        # ============ КОНЕЦ BIRTHDAY WELCOME ============

        # ============ BIRTHDAY DATE STEP — фиксированный вопрос про детей ============
        if session.intent == "birthday":
            # --- Обработка подтверждения телефона (web) ---
            session.lead_data = session.lead_data or {}
            last_bot_message = ""
            for msg in reversed(history):
                if msg.role == "assistant":
                    last_bot_message = (msg.content or "").lower()
                    break
            asked_phone_confirm = "актуален ли этот номер" in last_bot_message

            def _is_yes(t: str) -> bool:
                return bool(re.fullmatch(r"(да|ага|конечно|верно|правильно|ок|окей)", t))

            def _is_no(t: str) -> bool:
                return bool(re.fullmatch(r"(нет|неа|неверно|неправильно)", t))

            phone_confirmed_override = False
            awaiting_phone_input = session.lead_data.get("awaiting_phone_input")
            if awaiting_phone_input:
                phone_candidate = extract_phone_from_message(request.message)
                if phone_candidate and current_lead:
                    current_lead = update_lead_from_data(current_lead.id, {"phone": phone_candidate})
                    current_lead = _refresh_lead(db, current_lead)
                    lead_data = lead_to_dict(current_lead)
                    session.lead_data["phone_confirmed"] = True
                    session.lead_data.pop("awaiting_phone_input", None)
                    flag_modified(session, "lead_data")
                    db.commit()
                    phone_confirmed_override = True
                else:
                    session.lead_data["defer_phone_request"] = True
                    flag_modified(session, "lead_data")
                    db.commit()
            pending_phone = session.lead_data.get("pending_phone_confirm")
            if (not pending_phone and not session.lead_data.get("awaiting_phone_input") and asked_phone_confirm
                and lead_data and lead_data.get("phone") and not session.lead_data.get("phone_confirmed")):
                pending_phone = lead_data.get("phone")
                session.lead_data["pending_phone_confirm"] = pending_phone
                flag_modified(session, "lead_data")
                db.commit()

            if pending_phone:
                text = request.message.strip().lower()
                phone_candidate = extract_phone_from_message(request.message)
                if phone_candidate:
                    if current_lead:
                        current_lead = update_lead_from_data(current_lead.id, {"phone": phone_candidate})
                        current_lead = _refresh_lead(db, current_lead)
                        lead_data = lead_to_dict(current_lead)
                    session.lead_data["phone_confirmed"] = True
                    session.lead_data.pop("pending_phone_confirm", None)
                    session.lead_data.pop("awaiting_phone_input", None)
                    flag_modified(session, "lead_data")
                    db.commit()
                    phone_confirmed_override = True
                elif _is_yes(text):
                    session.lead_data["phone_confirmed"] = True
                    session.lead_data.pop("pending_phone_confirm", None)
                    flag_modified(session, "lead_data")
                    db.commit()
                    phone_confirmed_override = True
                elif _is_no(text):
                    session.lead_data.pop("pending_phone_confirm", None)
                    session.lead_data["phone_confirmed"] = False
                    session.lead_data["awaiting_phone_input"] = True
                    flag_modified(session, "lead_data")
                    db.commit()
                    response = "📱 Хорошо! Укажите актуальный номер телефона для связи:"
                    bot_message = Message(session_id=session.id, role="assistant", content=response)
                    db.add(bot_message)
                    db.commit()
                    db.close()
                    return ChatResponse(reply=response, session_id=session_id)
                else:
                    # Не распознали ответ — отвечаем и затем повторяем подтверждение
                    session.lead_data["defer_phone_confirm"] = True
                    flag_modified(session, "lead_data")
                    db.commit()

            # Если в сообщении есть дата — фиксируем и сразу спрашиваем про детей
            parsed_date = parsed_date_in_message or parse_user_date(request.message)
            if parsed_date and current_lead:
                normalized_date = format_date_ru(parsed_date, include_year=False)
                current_lead = update_lead_from_data(current_lead.id, {"event_date": normalized_date})
                current_lead = _refresh_lead(db, current_lead)
                lead_data = lead_to_dict(current_lead)
                session.lead_data = session.lead_data or {}
                session.lead_data["force_kids"] = True
                flag_modified(session, "lead_data")
                db.commit()
                response = build_birthday_date_question(parsed_date)
                bot_message = Message(session_id=session.id, role="assistant", content=response)
                db.add(bot_message)
                db.commit()
                db.close()
                return ChatResponse(reply=response, session_id=session_id)
            # Дата не распознана и ещё нет event_date — пометим для возврата после AI ответа
            elif not lead_data.get("event_date"):
                session.lead_data = session.lead_data or {}
                session.lead_data["defer_date_request"] = True
                flag_modified(session, "lead_data")
                db.commit()

            # Если дата есть, но детей ещё нет — задаём следующий вопрос с ценой
            force_kids = session.lead_data.get("force_kids") if session.lead_data else None
            defer_kids_request = False
            if lead_data and lead_data.get("event_date") and (force_kids or not lead_data.get("kids_count")):
                kids_count = parse_kids_count(request.message)
                if kids_count and current_lead:
                    current_lead = update_lead_from_data(current_lead.id, {"kids_count": kids_count})
                    current_lead = _refresh_lead(db, current_lead)
                    lead_data = lead_to_dict(current_lead)
                    if session.lead_data:
                        session.lead_data.pop("force_kids", None)
                        flag_modified(session, "lead_data")
                        db.commit()
                    force_kids = False
                elif request.message.strip():
                    session.lead_data = session.lead_data or {}
                    session.lead_data["defer_kids_request"] = True
                    flag_modified(session, "lead_data")
                    db.commit()
                    defer_kids_request = True

            if lead_data and lead_data.get("event_date") and (force_kids or not lead_data.get("kids_count")) and not defer_kids_request:
                date_obj = parse_user_date(lead_data["event_date"]) or parse_user_date(request.message)
                if date_obj:
                    response = build_birthday_date_question(date_obj)
                    bot_message = Message(session_id=session.id, role="assistant", content=response)
                    db.add(bot_message)
                    db.commit()
                    db.close()
                    return ChatResponse(reply=response, session_id=session_id)

            # Если ждём телефон и пользователь прислал его — сохраняем без LLM
            if lead_data and lead_data.get("event_date") and lead_data.get("kids_count") and not lead_data.get("phone"):
                phone_candidate = extract_phone_from_message(request.message)
                if phone_candidate and current_lead:
                    current_lead = update_lead_from_data(current_lead.id, {"phone": phone_candidate})
                    current_lead = _refresh_lead(db, current_lead)
                    lead_data = lead_to_dict(current_lead)
                    session.lead_data = session.lead_data or {}
                    session.lead_data["phone_confirmed"] = True
                    flag_modified(session, "lead_data")
                    db.commit()

            # Если пользователь выбрал формат — фиксируем без LLM
            format_candidate = None
            if lead_data and lead_data.get("event_date") and lead_data.get("kids_count"):
                format_candidate = extract_format_from_message(request.message)
                if format_candidate and current_lead and not lead_data.get("format"):
                    current_lead = update_lead_from_data(current_lead.id, {"format": format_candidate})
                    current_lead = _refresh_lead(db, current_lead)
                    lead_data = lead_to_dict(current_lead)

            # Если сделка уже есть — синхронизируем поля после обновлений
            if current_lead and current_lead.amocrm_deal_id:
                try:
                    await amocrm_client.update_deal_fields(int(current_lead.amocrm_deal_id), lead_data)
                except Exception as e:
                    logger.error(f"Failed to update AmoCRM deal (web): {e}")

            session.lead_data = session.lead_data or {}
            phone_confirmed = phone_confirmed_override or bool(session.lead_data.get("phone_confirmed"))
            pending_phone = session.lead_data.get("pending_phone_confirm")
            phone_value = lead_data.get("phone")
            effective_phone = phone_value if phone_confirmed else None

            # Если телефон есть, но не подтвержден — запросить подтверждение
            if lead_data and lead_data.get("event_date") and lead_data.get("kids_count") and phone_value and not phone_confirmed and not pending_phone:
                session.lead_data["pending_phone_confirm"] = phone_value
                flag_modified(session, "lead_data")
                db.commit()
                response = f"📱 Актуален ли этот номер телефона для связи?\n{phone_value}\n\nОтветьте: да/нет."
                bot_message = Message(session_id=session.id, role="assistant", content=response)
                db.add(bot_message)
                db.commit()
                db.close()
                return ChatResponse(reply=response, session_id=session_id)

            # Если дата и дети уже есть, но телефона нет — спрашиваем телефон
            if lead_data and lead_data.get("event_date") and lead_data.get("kids_count") and not effective_phone:
                if should_defer_phone_request(request.message):
                    session.lead_data = session.lead_data or {}
                    session.lead_data["defer_phone_request"] = True
                    flag_modified(session, "lead_data")
                    db.commit()
                else:
                    response = "📱 Оставьте номер телефона для связи:"
                    bot_message = Message(session_id=session.id, role="assistant", content=response)
                    db.add(bot_message)
                    db.commit()
                    db.close()
                    return ChatResponse(reply=response, session_id=session_id)

            # Если дата, дети и телефон есть — создаём сделку (если ещё нет) и задаём короткие вопросы
            if lead_data and lead_data.get("event_date") and lead_data.get("kids_count") and effective_phone:
                if current_lead and not current_lead.amocrm_deal_id:
                    try:
                        from core.amocrm import send_lead_to_amocrm
                        from core.lead_service import save_amocrm_deal_id
                        
                        lead_dict = lead_to_dict(current_lead)
                        lead_dict["source"] = "web"
                        deal_id, contact_id = await send_lead_to_amocrm(
                            lead_data=lead_dict,
                            telegram_id=None,
                            username=None
                        )
                        if deal_id:
                            save_amocrm_deal_id(current_lead.id, str(deal_id))
                            current_lead.amocrm_deal_id = str(deal_id)
                            msg_text = format_lead_message("web", session_id, lead_dict)
                            await send_to_birthday_channel(msg_text)
                            mark_lead_sent_to_manager(current_lead.id)
                            # Добавляем историю переписки в AmoCRM
                            try:
                                chat_history_text = "\n".join(
                                    [f"{'Клиент' if m.role == 'user' else 'Бот'}: {m.content}" for m in history]
                                )
                                await amocrm_client.add_note(int(deal_id), f"📱 История переписки (веб-чат):\n\n{chat_history_text}")
                            except Exception as ne:
                                logger.error(f"Failed to add chat history note: {ne}")
                    except Exception as e:
                        logger.error(f"Failed to send web lead to AmoCRM: {e}")
                
                # Короткие вопросы по формату / времени / имени
                format_value = (lead_data.get("format") or "").strip().lower()
                is_room = "комнат" in format_value or "room" in format_value
                
                if not format_value:
                    session.lead_data = session.lead_data or {}
                    session.lead_data["defer_format_request"] = True
                    flag_modified(session, "lead_data")
                    db.commit()
                
                if is_room and not lead_data.get("time"):
                    if format_candidate:
                        response = "⏰ На какое время? Слоты: 10:30, 14:30, 18:30"
                        bot_message = Message(session_id=session.id, role="assistant", content=response)
                        db.add(bot_message)
                        db.commit()
                        db.close()
                        return ChatResponse(reply=response, session_id=session_id)
                    session.lead_data = session.lead_data or {}
                    session.lead_data["defer_time_request"] = True
                    flag_modified(session, "lead_data")
                    db.commit()
                
                if not lead_data.get("customer_name"):
                    response = "👤 Как к вам обращаться?"
                    bot_message = Message(session_id=session.id, role="assistant", content=response)
                    db.add(bot_message)
                    db.commit()
                    db.close()
                    return ChatResponse(reply=response, session_id=session_id)
        # ============ КОНЕЦ BIRTHDAY DATE STEP ============
        
        # Проверяем запрос живого менеджера ПЕРЕД генерацией ответа
        is_manager_request = needs_human_escalation(request.message)
        
        if is_manager_request:
            # Получаем данные пользователя из сессии
            user_name = session.lead_data.get("customer_name", "Не указано") if session.lead_data else "Не указано"
            user_phone = session.lead_data.get("phone", "Не указан") if session.lead_data else "Не указан"
            
            # Формируем специальный ответ
            response = (
                f"Хорошо! 📞 Передаю ваш запрос менеджеру.\\n\\n"
                f"Мы свяжемся с вами по номеру {user_phone} в ближайшее время.\\n\\n"
                "Если номер неактуален — напишите новый, и я передам его менеджеру."
            )
            
            # Формируем историю чата
            chat_history_text = "\\n".join([
                f"{'Клиент' if m.role == 'user' else 'Бот'}: {m.content}"
                for m in history
            ])
            
            # Отправляем в AmoCRM
            try:
                from core.amocrm import send_lead_to_amocrm, AmoCRMClient
                
                deal_id, contact_id = await send_lead_to_amocrm(
                    lead_data={
                        "customer_name": user_name,
                        "phone": user_phone,
                        "source": "web",
                        "notes": "Клиент запросил живого менеджера"
                    },
                    telegram_id=None,
                    username=None
                )
                
                if deal_id:
                    # Добавляем историю чата как примечание
                    try:
                        amocrm_instance = AmoCRMClient()
                        note_text = f"📱 История переписки перед запросом менеджера:\\n\\n{chat_history_text}"
                        await amocrm_instance.add_note(int(deal_id), note_text)
                    except Exception as ne:
                        logger.error(f"Failed to add chat history note: {ne}")
                    logger.info(f"Manager request sent to AmoCRM, deal_id={deal_id}")
            except Exception as e:
                logger.error(f"Failed to send manager request to AmoCRM: {e}")
            
            # Уведомляем менеджеров
            manager_msg = (
                f"📞 <b>ЗАПРОС ЖИВОГО МЕНЕДЖЕРА</b>\\n\\n"
                f"👤 <b>Имя:</b> {user_name}\\n"
                f"📱 <b>Телефон:</b> {user_phone}\\n"
                f"🌐 <b>Источник:</b> Веб-чат\\n\\n"
                f"💬 <b>Последние сообщения:</b>\\n"
                f"{chat_history_text[-500:] if len(chat_history_text) > 500 else chat_history_text}"
            )
            await send_to_managers(manager_msg)
            logger.info(f"Manager request notification sent for web user {user_name}")
        else:
            # Генерируем обычный ответ
            response = agent.generate_response(
                message=request.message,
                intent=session.intent,
                rag_context=rag_context,
                history=history_list,
                lead_data=lead_data
            )

            # Если откладывали запрос телефона — добавляем после ответа
            if session.intent == "birthday":
                defer_phone = None
                if session.lead_data and session.lead_data.get("defer_phone_request"):
                    defer_phone = True
                    session.lead_data.pop("defer_phone_request", None)
                    flag_modified(session, "lead_data")
                    db.commit()
                if defer_phone and lead_data and not lead_data.get("phone"):
                    if "телефон" not in response.lower() and "номер" not in response.lower():
                        response += "\n\n📱 Оставьте номер телефона для связи, чтобы мы закрепили бронирование."

                defer_phone_confirm = None
                if session.lead_data and session.lead_data.get("defer_phone_confirm"):
                    defer_phone_confirm = True
                    session.lead_data.pop("defer_phone_confirm", None)
                    flag_modified(session, "lead_data")
                    db.commit()
                if defer_phone_confirm and lead_data and lead_data.get("phone"):
                    phone_confirmed = phone_confirmed_override or bool(session.lead_data.get("phone_confirmed"))
                    if not phone_confirmed:
                        response += f"\n\n📱 Актуален ли этот номер телефона для связи?\n{lead_data.get('phone')}\n\nОтветьте: да/нет."

                defer_format = None
                if session.lead_data and session.lead_data.get("defer_format_request"):
                    defer_format = True
                    session.lead_data.pop("defer_format_request", None)
                    flag_modified(session, "lead_data")
                    db.commit()
                if defer_format and lead_data and lead_data.get("event_date") and lead_data.get("kids_count") and not lead_data.get("format"):
                    format_msg = build_format_choice_message(lead_data.get("event_date"), lead_data.get("kids_count"))
                    if format_msg and format_msg.lower() not in response.lower():
                        response += "\n\n" + format_msg

                defer_kids = None
                if session.lead_data and session.lead_data.get("defer_kids_request"):
                    defer_kids = True
                    session.lead_data.pop("defer_kids_request", None)
                    flag_modified(session, "lead_data")
                    db.commit()
                if defer_kids and lead_data and lead_data.get("event_date") and not lead_data.get("kids_count"):
                    date_obj = parse_user_date(lead_data["event_date"])
                    if date_obj:
                        kids_msg = build_birthday_date_question(date_obj)
                        if kids_msg and kids_msg.lower() not in response.lower():
                            response += "\n\n" + kids_msg

                defer_time = None
                if session.lead_data and session.lead_data.get("defer_time_request"):
                    defer_time = True
                    session.lead_data.pop("defer_time_request", None)
                    flag_modified(session, "lead_data")
                    db.commit()
                if defer_time and lead_data and lead_data.get("event_date") and lead_data.get("kids_count") and lead_data.get("format"):
                    format_value = (lead_data.get("format") or "").strip().lower()
                    is_room = "комнат" in format_value or "room" in format_value
                    if is_room and not lead_data.get("time"):
                        if "слот" not in response.lower() and "время" not in response.lower():
                            response += "\n\n⏰ На какое время? Слоты: 10:30, 14:30, 18:30"

                # Обработка defer_date_request — напоминание о дате
                defer_date = None
                if session.lead_data and session.lead_data.get("defer_date_request"):
                    defer_date = True
                    session.lead_data.pop("defer_date_request", None)
                    flag_modified(session, "lead_data")
                    db.commit()
                if defer_date and lead_data and not lead_data.get("event_date"):
                    if "дат" not in response.lower() and "когда" not in response.lower():
                        response += "\n\n📅 На какую дату планируете праздник?"
        
        # Сохраняем ответ бота
        bot_message = Message(
            session_id=session.id,
            role="assistant",
            content=response
        )
        db.add(bot_message)
        db.commit()
        
        # Отправляем уведомление менеджеру и в AmoCRM если нужно
        if current_lead and not current_lead.sent_to_manager:
            if any(x in response.lower() for x in ["передал", "передаю заявку", "менеджер свяжется", "отдел праздников"]):
                # НЕ извлекаем данные из ответа бота! Это вызывало баг с extras
                # (бот говорит "аниматор, торт, шары — по желанию" и система думала что клиент их заказал)
                # Данные уже были извлечены из сообщений пользователя в lead_data
                current_lead = update_lead_from_data(current_lead.id, lead_data)
                current_lead = _refresh_lead(db, current_lead)
                
                # Формируем историю чата для AmoCRM
                chat_history_text = "\\n".join([
                    f"{'Клиент' if m.role == 'user' else 'Бот'}: {m.content}"
                    for m in history
                ])
                
                # Отправляем в AmoCRM
                try:
                    from core.amocrm import send_lead_to_amocrm, AmoCRMClient
                    from core.lead_service import save_amocrm_deal_id
                    
                    lead_dict = lead_to_dict(current_lead)
                    lead_dict["chat_history"] = chat_history_text
                    deal_id, contact_id = await send_lead_to_amocrm(
                        lead_data=lead_dict,
                        telegram_id=None,
                        username=None
                    )
                    
                    if deal_id:
                        save_amocrm_deal_id(current_lead.id, str(deal_id))
                        # Добавляем историю чата как примечание
                        try:
                            amocrm_instance = AmoCRMClient()
                            note_text = f"📱 История переписки (веб-чат):\\n\\n{chat_history_text}"
                            await amocrm_instance.add_note(int(deal_id), note_text)
                        except Exception as ne:
                            logger.error(f"Failed to add chat history note: {ne}")
                        logger.info(f"Web lead #{current_lead.id} sent to AmoCRM, deal_id={deal_id}")
                except Exception as e:
                    logger.error(f"Failed to send web lead to AmoCRM: {e}")
                
                # Уведомляем менеджеров
                msg_text = format_lead_message("web", session_id, lead_to_dict(current_lead))
                await send_to_birthday_channel(msg_text)
                mark_lead_sent_to_manager(current_lead.id)
                logger.info(f"Manager notification sent for Lead #{current_lead.id}")
        
        db.close()
        
        # Определяем, нужны ли кнопки в ответе
        # Логика на основе состояния данных, а не текста
        buttons = None
        
        # Если есть дата, количество детей и телефон, но нет формата — показываем выбор формата
        if (lead_data.get("event_date") and 
            lead_data.get("kids_count") and 
            lead_data.get("phone") and 
            not lead_data.get("format")):
            buttons = [
                {"text": "🏠 Комната", "callback": "format_room"},
                {"text": "🍰 Ресторан", "callback": "format_zone"}
            ]
        # Если выбрана комната, но нет времени — показываем слоты
        elif (lead_data.get("format") and 
              "комнат" in (lead_data.get("format") or "").lower() and 
              not lead_data.get("time")):
            buttons = [
                {"text": "🕥 10:30", "callback": "time_10_30"},
                {"text": "🕝 14:30", "callback": "time_14_30"},
                {"text": "🕡 18:30", "callback": "time_18_30"}
            ]
        # Fallback: проверяем текст ответа, но только если формат/время ещё не выбраны
        else:
            response_lower = response.lower()
            # Показываем кнопки формата только если он ещё не выбран
            if not lead_data.get("format"):
                if (("комнат" in response_lower and "ресторан" in response_lower) or 
                    "формат праздника" in response_lower or 
                    "какой формат" in response_lower):
                    buttons = [
                        {"text": "🏠 Комната", "callback": "format_room"},
                        {"text": "🍰 Ресторан", "callback": "format_zone"}
                    ]
            # Показываем кнопки времени только если формат = комната и время ещё не выбрано
            if not buttons and lead_data.get("format") and "комнат" in (lead_data.get("format") or "").lower() and not lead_data.get("time"):
                if "слот" in response_lower or ("время" in response_lower and any(x in response_lower for x in ["10:30", "14:30", "18:30"])):
                    buttons = [
                        {"text": "🕥 10:30", "callback": "time_10_30"},
                        {"text": "🕝 14:30", "callback": "time_14_30"},
                        {"text": "🕡 18:30", "callback": "time_18_30"}
                    ]

        
        return ChatResponse(
            reply=response,
            session_id=session_id,
            buttons=buttons
        )
        
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# AmoCRM Integration Endpoints
# =============================================================================

@app.get("/amocrm/callback", response_class=HTMLResponse)
async def amocrm_callback(code: Optional[str] = None, error: Optional[str] = None):
    """
    OAuth2 callback from AmoCRM.
    Exchanges authorization code for access tokens.
    """
    if error:
        return HTMLResponse(f"""
            <html><body>
            <h1>❌ Ошибка авторизации AmoCRM</h1>
            <p>{error}</p>
            </body></html>
        """)
    
    if not code:
        # Redirect to AmoCRM auth
        auth_url = amocrm_client.get_auth_url()
        return HTMLResponse(f"""
            <html><body>
            <h1>🔐 Авторизация AmoCRM</h1>
            <p><a href="{auth_url}">Нажмите для авторизации</a></p>
            </body></html>
        """)
    
    # Exchange code for tokens
    success = await amocrm_client.exchange_code_for_tokens(code)
    
    if success:
        return HTMLResponse("""
            <html><body>
            <h1>✅ Авторизация успешна!</h1>
            <p>AmoCRM интеграция активирована. Можете закрыть это окно.</p>
            </body></html>
        """)
    else:
        return HTMLResponse("""
            <html><body>
            <h1>❌ Ошибка получения токена</h1>
            <p>Попробуйте авторизоваться снова.</p>
            </body></html>
        """)


@app.post("/amocrm/webhook")
async def amocrm_webhook(request: Request):
    """
    Webhook from AmoCRM for deal status changes.
    Notifies users via Telegram when their booking is updated.
    """
    try:
        # Parse form data (AmoCRM sends as form-urlencoded)
        form_data = await request.form()
        data = dict(form_data)
        logger.info(f"AmoCRM webhook received: {data}")
        
        # Check for deal (lead) update event
        # AmoCRM sends data like: leads[update][0][id], leads[update][0][status_id], etc.
        
        # Parse the webhook data
        deal_id = None
        new_status_id = None
        
        for key in data.keys():
            if 'leads[update]' in key or 'leads[status]' in key:
                if '[id]' in key:
                    deal_id = data[key]
                elif '[status_id]' in key:
                    new_status_id = data[key]
        
        if not deal_id:
            logger.info("No deal update in webhook, ignoring")
            return {"status": "ok"}
        
        logger.info(f"Deal {deal_id} status changed to {new_status_id}")
        
        # Find lead by amocrm_deal_id
        db = SessionLocal()
        lead = db.query(Lead).filter(Lead.amocrm_deal_id == str(deal_id)).first()
        
        if not lead:
            logger.warning(f"Lead with amocrm_deal_id={deal_id} not found")
            db.close()
            return {"status": "ok"}
        
        # Get status name (you can map status_id to names)
        # For now, we'll fetch deal info from AmoCRM
        deal_info = await amocrm_client.get_deal(int(deal_id))
        
        if deal_info:
            status_name = deal_info.get("status_id", "неизвестен")
            # TODO: Map status_id to human-readable name
            
            # Notify user via Telegram
            telegram_id = lead.telegram_id
            if telegram_id and telegram_id.isdigit():
                await notify_telegram_user(
                    telegram_id,
                    f"📋 Обновление по вашей заявке!\n\n"
                    f"Статус бронирования изменён.\n"
                    f"Дата: {lead.event_date or 'не указана'}\n\n"
                    f"Если у вас есть вопросы, напишите нам!"
                )
                logger.info(f"Notified user {telegram_id} about deal {deal_id} update")
        
        db.close()
        return {"status": "ok"}
        
    except Exception as e:
        logger.error(f"AmoCRM webhook error: {e}")
        return {"status": "error", "message": str(e)}


@app.get("/amocrm/status")
async def amocrm_status():
    """Check AmoCRM integration status."""
    return {
        "authorized": amocrm_client.is_authorized,
        "domain": amocrm_client.domain,
        "auth_url": amocrm_client.get_auth_url() if not amocrm_client.is_authorized else None
    }


async def notify_telegram_user(chat_id: str, message: str):
    """Send notification to user via Telegram."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        logger.error("TELEGRAM_BOT_TOKEN not configured")
        return
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML"
            })
            if response.status_code != 200:
                logger.error(f"Failed to send Telegram message: {response.text}")
        except Exception as e:
            logger.error(f"Error sending Telegram message: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
