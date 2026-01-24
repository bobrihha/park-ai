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
)
from db.database import SessionLocal
from db.models import Session as DBSession, Message, Lead
from sqlalchemy.orm.attributes import flag_modified
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
    format_complaint_message
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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


class ChatResponse(BaseModel):
    reply: str
    session_id: str


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
        
        # Получаем историю сообщений
        history = db.query(Message).filter(
            Message.session_id == session.id
        ).order_by(Message.id).all()
        
        history_list = [{"role": m.role, "content": m.content} for m in history]
        
        # Получаем RAG контекст
        rag_context = rag.get_context(request.message, session.intent)
        
        # Для birthday — работаем с Lead
        lead_data = {}
        current_lead = None
        
        if session.intent == "birthday":
            current_lead = get_or_create_lead(session_id, source="web", park_id="nn")
            
            # Используем данные из регистрации (если есть)
            if session.lead_data and session.lead_data.get("web_registered"):
                if not current_lead.customer_name and session.lead_data.get("customer_name"):
                    current_lead = update_lead_from_data(current_lead.id, {
                        "customer_name": session.lead_data.get("customer_name")
                    })
                if not current_lead.phone and session.lead_data.get("phone"):
                    current_lead = update_lead_from_data(current_lead.id, {
                        "phone": session.lead_data.get("phone")
                    })
            
            # Также используем данные из запроса (если пользователь указал новые)
            if request.user_name and not current_lead.customer_name:
                current_lead = update_lead_from_data(current_lead.id, {"customer_name": request.user_name})
            if request.user_phone and not current_lead.phone:
                current_lead = update_lead_from_data(current_lead.id, {"phone": request.user_phone})
            
            # Извлекаем данные из сообщения
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
            if extracted:
                current_lead = update_lead_from_data(current_lead.id, extracted)
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
        
        # ============ BIRTHDAY WELCOME — приветственное сообщение как в TG/VK ============
        if intent_just_switched_to_birthday:
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
            # --- Если пользователь явно хочет начать новую бронь — создаём новый lead ---
            text_lower = request.message.lower().strip()
            change_keywords = ["изменить", "перенести", "поменять", "другую дату", "сменить"]
            start_keywords = ["хочу организовать", "хочу забронировать", "забронировать праздник", "организовать день рождения", "хочу праздник"]
            start_new_booking = any(k in text_lower for k in start_keywords) and not any(k in text_lower for k in change_keywords)
            if start_new_booking and current_lead and (current_lead.event_date or current_lead.kids_count):
                current_lead = force_create_new_lead(session_id, park_id="nn", source="web")
                lead_data = lead_to_dict(current_lead)
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

            # --- Обработка подтверждения телефона (web) ---
            last_bot_message = ""
            for msg in reversed(history):
                if msg.role == "assistant":
                    last_bot_message = (msg.content or "").lower()
                    break
            asked_phone_confirm = "актуален ли этот номер" in last_bot_message

            def _is_yes(t: str) -> bool:
                return bool(re.search(r"\bда\b|ага|конечно|верно|правильно|ок\b|окей", t))

            def _is_no(t: str) -> bool:
                return bool(re.search(r"\bнет\b|неа|неверно|неправильно", t))

            phone_confirmed_override = False

            if (session.lead_data and session.lead_data.get("pending_phone_confirm")) or asked_phone_confirm:
                pending_phone = session.lead_data.get("pending_phone_confirm")
                text = request.message.strip().lower()
                if _is_yes(text):
                    session.lead_data["phone_confirmed"] = True
                    session.lead_data.pop("pending_phone_confirm", None)
                    flag_modified(session, "lead_data")
                    db.commit()
                    phone_confirmed_override = True
                elif _is_no(text):
                    session.lead_data.pop("pending_phone_confirm", None)
                    session.lead_data["phone_confirmed"] = False
                    flag_modified(session, "lead_data")
                    db.commit()
                    response = "📱 Хорошо! Укажите актуальный номер телефона для связи:"
                    bot_message = Message(session_id=session.id, role="assistant", content=response)
                    db.add(bot_message)
                    db.commit()
                    db.close()
                    return ChatResponse(reply=response, session_id=session_id)

            # Если в сообщении есть дата — фиксируем и сразу спрашиваем про детей
            parsed_date = parse_user_date(request.message)
            if parsed_date and current_lead:
                normalized_date = format_date_ru(parsed_date, include_year=False)
                current_lead = update_lead_from_data(current_lead.id, {"event_date": normalized_date})
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

            # Если дата есть, но детей ещё нет — задаём следующий вопрос с ценой
            force_kids = session.lead_data.get("force_kids") if session.lead_data else None
            if lead_data and lead_data.get("event_date") and (force_kids or not lead_data.get("kids_count")):
                kids_count = parse_kids_count(request.message)
                if kids_count and current_lead:
                    current_lead = update_lead_from_data(current_lead.id, {"kids_count": kids_count})
                    lead_data = lead_to_dict(current_lead)
                    if session.lead_data:
                        session.lead_data.pop("force_kids", None)
                        flag_modified(session, "lead_data")
                        db.commit()

            if lead_data and lead_data.get("event_date") and (force_kids or not lead_data.get("kids_count")):
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
                    lead_data = lead_to_dict(current_lead)
                    session.lead_data = session.lead_data or {}
                    session.lead_data["phone_confirmed"] = True
                    flag_modified(session, "lead_data")
                    db.commit()

            # Если пользователь выбрал формат — фиксируем без LLM
            if lead_data and lead_data.get("event_date") and lead_data.get("kids_count"):
                format_candidate = extract_format_from_message(request.message)
                if format_candidate and current_lead and not lead_data.get("format"):
                    current_lead = update_lead_from_data(current_lead.id, {"format": format_candidate})
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
                    response = build_format_choice_message(lead_data.get("event_date"), lead_data.get("kids_count")) \
                        or "🎉 Какой формат праздника предпочитаете — тематическая комната или столик в ресторане?"
                    bot_message = Message(session_id=session.id, role="assistant", content=response)
                    db.add(bot_message)
                    db.commit()
                    db.close()
                    return ChatResponse(reply=response, session_id=session_id)
                
                if is_room and not lead_data.get("time"):
                    response = "⏰ На какое время? Слоты: 10:30, 14:30, 18:30"
                    bot_message = Message(session_id=session.id, role="assistant", content=response)
                    db.add(bot_message)
                    db.commit()
                    db.close()
                    return ChatResponse(reply=response, session_id=session_id)
                
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
                        amocrm_client = AmoCRMClient()
                        note_text = f"📱 История переписки перед запросом менеджера:\\n\\n{chat_history_text}"
                        await amocrm_client.add_note(int(deal_id), note_text)
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
                            amocrm_client = AmoCRMClient()
                            note_text = f"📱 История переписки (веб-чат):\\n\\n{chat_history_text}"
                            await amocrm_client.add_note(int(deal_id), note_text)
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
        
        return ChatResponse(
            reply=response,
            session_id=session_id
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
