"""
MessageService — единый сервис обработки сообщений для всех платформ.

Использует модульную архитектуру flows для специализированных сценариев.
Платформо-специфичный код (TG/VK/Web) остаётся в обработчиках.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Any
from enum import Enum

from db import SessionLocal, Session as DBSession, Message, Lead
from sqlalchemy.orm.attributes import flag_modified

from core.agent import Agent
from core.rag import RAGSystem
from core.flows.base import FlowContext, FlowResult, FlowStatus
from core.flows.complaint import ComplaintFlow
from core.flows.lost_item import LostItemFlow
from core.flows.birthday import BirthdayFlow
from core.flows.photo import PhotoFlow
from core.flows.partnership import PartnershipFlow
from core.flows.booking import BookingQueryFlow, BookingChangeFlow
from core.notifications import send_to_managers, needs_human_escalation
from config.park_config import get_current_park_id

logger = logging.getLogger(__name__)


# ============ DATA CLASSES ============

class Platform(Enum):
    TELEGRAM = "telegram"
    VK = "vk"
    WEB = "web"


@dataclass
class UserInfo:
    """Информация о пользователе."""
    user_id: str
    platform: Platform
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    
    @property
    def display_name(self) -> str:
        if self.first_name:
            return self.first_name
        if self.username:
            return f"@{self.username}"
        return "Гость"


@dataclass
class MessageResult:
    """Результат обработки сообщения."""
    text: str
    buttons: List[dict] = field(default_factory=list)
    image_path: Optional[str] = None
    should_notify_manager: bool = False
    manager_message: Optional[str] = None
    manager_channel: str = "general"  # "general" | "birthday"
    lead_data: Optional[dict] = None


# ============ MESSAGE SERVICE ============

class MessageService:
    """
    Единый сервис обработки сообщений.
    
    Использование:
        service = MessageService(park_id="nn")
        result = await service.process_message(user_info, message_text)
    """
    
    def __init__(self, park_id: Optional[str] = None):
        self.park_id = park_id or get_current_park_id()
        self.agent = Agent()
        self.rag = RAGSystem(park_id=self.park_id)
        
        # Инициализация flows (порядок важен!)
        self.flows = [
            ComplaintFlow(),          # Жалобы
            LostItemFlow(),           # Потеряшки
            PhotoFlow(),              # Фото запросы
            PartnershipFlow(),        # Партнёрство
            BookingQueryFlow(),       # Показ бронирований
            BookingChangeFlow(),      # Изменение бронирований
            BirthdayFlow(agent=self.agent),  # Birthday flow (последний — fallback для birthday intent)
        ]
    
    async def process_message(
        self, 
        user_info: UserInfo, 
        message_text: str
    ) -> MessageResult:
        """
        Главный метод — обрабатывает сообщение и возвращает результат.
        """
        db = SessionLocal()
        try:
            # 1. Получаем или создаём сессию
            session = self._get_or_create_session(db, user_info)
            
            # 2. Сохраняем входящее сообщение
            self._save_message(db, session.id, "user", message_text)
            
            # 3. Создаём контекст
            context = FlowContext(
                user_id=user_info.user_id,
                platform=user_info.platform.value,
                username=user_info.username,
                first_name=user_info.first_name,
                message_text=message_text,
                session_id=session.id,
                intent=session.intent or "unknown",
                lead_data=session.lead_data or {},
                park_id=self.park_id
            )
            
            # 4. Проверяем специальные handlers
            result = await self._check_special_handlers(db, session, context, user_info, message_text)
            if result:
                self._save_message(db, session.id, "assistant", result.text)
                return result
            
            # 5. Пробуем flows
            for flow in self.flows:
                flow_result = await flow.process(context)
                
                if flow_result.status == FlowStatus.HANDLED:
                    # Flow обработал — конвертируем и применяем
                    result = self._flow_result_to_message_result(flow_result)
                    
                    # Обновляем сессию если нужно
                    if flow_result.update_session:
                        if "intent" in flow_result.update_session:
                            session.intent = flow_result.update_session["intent"]
                        if "lead_data" in flow_result.update_session:
                            session.lead_data = flow_result.update_session["lead_data"]
                            flag_modified(session, "lead_data")
                        db.commit()
                    
                    self._save_message(db, session.id, "assistant", result.text)
                    return result
                
                elif flow_result.status == FlowStatus.DEFER_TO_AI:
                    # Flow не смог распознать — передаём AI, но сохраняем шаг для возврата
                    session.lead_data = session.lead_data or {}
                    session.lead_data["pending_step"] = flow_result.pending_step
                    session.lead_data["pending_prompt"] = flow_result.pending_prompt
                    flag_modified(session, "lead_data")
                    db.commit()
                    
                    # Обновляем контекст с pending данными
                    context.lead_data = session.lead_data
                    break  # Передаём AI
            
            # 6. Никакой flow не обработал — AI агент
            result = await self._handle_ai_response(db, session, context, user_info, message_text)
            self._save_message(db, session.id, "assistant", result.text)
            return result

            
        finally:
            db.close()
    
    async def _check_special_handlers(
        self, 
        db, 
        session, 
        context: FlowContext,
        user_info: UserInfo, 
        message_text: str
    ) -> Optional[MessageResult]:
        """Проверка специальных handlers (app_id, human escalation)."""
        
        # App ID
        result = self._check_app_id(user_info, message_text)
        if result:
            return result
        
        # Human escalation
        if needs_human_escalation(message_text):
            return MessageResult(
                text="Понимаю, что вам нужна помощь живого менеджера! 🙋\n\nЯ уже передал ваш запрос нашей команде. Менеджер свяжется с вами в ближайшее время!\n\nА пока я могу ответить на ваши вопросы о парке или празднике. 😊",
                should_notify_manager=True,
                manager_message=f"🙋 <b>Запрос менеджера!</b>\n\n👤 {user_info.display_name}\n💬 {message_text}"
            )
        
        return None
    
    def _check_app_id(self, user_info: UserInfo, message_text: str) -> Optional[MessageResult]:
        """Проверка App ID для программы лояльности."""
        if re.search(r'[\+\(\)]', message_text):
            return None
        if re.search(r'\d{1,3}\-\d{1,3}\-\d{1,3}', message_text):
            return None
            
        id_keywords = r'(?:app\s*id|мой\s*id|айди|ид\b|код|подписал\w*|подписка)'
        app_id_match = re.search(id_keywords + r'\s*[,:.=\-]?\s*(\d{5,8})\b', message_text, re.IGNORECASE)
        
        if not app_id_match:
            clean_text = message_text.strip()
            if len(clean_text) <= 12:
                bare_id_match = re.match(r'^(\d{5,8})$', clean_text)
                if bare_id_match:
                    potential_id = bare_id_match.group(1)
                    if not (potential_id[0] in '789' and len(potential_id) >= 10):
                        app_id_match = bare_id_match
        
        if not app_id_match:
            return None
            
        app_id = app_id_match.group(1)
        
        return MessageResult(
            text="Принято! Передал менеджеру для начисления баллов. Баллы будут начислены в течение 7 дней. Спасибо, что вы с нами! 💚💜",
            should_notify_manager=True,
            manager_message=f"🔔 <b>Новый App ID!</b>\n\n👤 {user_info.display_name}\n🔢 ID: <code>{app_id}</code>\n💬 {message_text}"
        )
    
    def _flow_result_to_message_result(self, flow_result: FlowResult) -> MessageResult:
        """Конвертация FlowResult в MessageResult."""
        return MessageResult(
            text=flow_result.text or "",
            buttons=flow_result.buttons,
            image_path=flow_result.image_path,
            should_notify_manager=flow_result.should_notify_manager,
            manager_message=flow_result.manager_message,
            manager_channel=flow_result.manager_channel,
            lead_data=flow_result.lead_data
        )
    
    async def _handle_ai_response(
        self, 
        db, 
        session, 
        context: FlowContext,
        user_info: UserInfo, 
        message_text: str
    ) -> MessageResult:
        """Генерация ответа через AI агента."""
        try:
            # Получаем историю
            history = self._get_history(db, session.id)
            
            # Получаем контекст из RAG
            rag_context = self.rag.search(message_text, n_results=3)
            context_text = "\n".join([doc.get("content", "") for doc in rag_context]) if rag_context else ""
            
            # Генерируем ответ через Agent
            response = self.agent.generate_response(
                message=message_text,
                intent=context.intent or "general",
                history=history,
                rag_context=context_text,
                lead_data=context.lead_data
            )
            
            # Если был отложенный шаг — добавляем напоминание и очищаем
            pending_prompt = context.lead_data.get("pending_prompt") if context.lead_data else None
            if pending_prompt:
                # Добавляем мягкое напоминание о текущем шаге
                response += f"\n\n{pending_prompt}"
                
                # Очищаем pending из сессии
                if session.lead_data:
                    session.lead_data.pop("pending_step", None)
                    session.lead_data.pop("pending_prompt", None)
                    flag_modified(session, "lead_data")
                    db.commit()
            
            return MessageResult(text=response)
            
        except Exception as e:
            logger.error(f"AI response error: {e}")
            return MessageResult(
                text="Упс, что-то пошло не так! 😅 Попробуйте ещё раз или напишите /human для связи с менеджером."
            )

    
    # ============ HELPERS ============
    
    def _get_or_create_session(self, db, user_info: UserInfo) -> DBSession:
        """Получить или создать сессию пользователя."""
        session = db.query(DBSession).filter(DBSession.telegram_id == user_info.user_id).first()
        
        if not session:
            session = DBSession(
                telegram_id=user_info.user_id,
                park_id=self.park_id,
                username=user_info.username
            )
            db.add(session)
            db.commit()
            db.refresh(session)
        else:
            if user_info.username and session.username != user_info.username:
                session.username = user_info.username
                db.commit()
        
        return session
    
    def _save_message(self, db, session_id: int, role: str, content: str):
        """Сохранить сообщение в истории."""
        msg = Message(session_id=session_id, role=role, content=content)
        db.add(msg)
        db.commit()
    
    def _get_history(self, db, session_id: int, limit: int = 10) -> List[dict]:
        """Получить историю сообщений для контекста."""
        messages = db.query(Message).filter(
            Message.session_id == session_id
        ).order_by(Message.id.desc()).limit(limit).all()
        
        return [
            {"role": msg.role, "content": msg.content}
            for msg in reversed(messages)
        ]


# ============ SINGLETON ============

_service_instance: Optional[MessageService] = None

def get_message_service(park_id: Optional[str] = None) -> MessageService:
    """Получить singleton инстанс MessageService."""
    global _service_instance
    
    if _service_instance is None or (park_id and _service_instance.park_id != park_id):
        _service_instance = MessageService(park_id)
    
    return _service_instance
