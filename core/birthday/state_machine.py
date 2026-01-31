"""
Birthday Flow State Machine — централизованное управление состояниями.

Единый источник правды для текущего шага flow.
"""

import logging
from enum import Enum
from typing import Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime

from db import SessionLocal, Lead
from sqlalchemy.orm.attributes import flag_modified

logger = logging.getLogger(__name__)


class BirthdayState(Enum):
    """Шаги birthday flow — ЕДИНЫЙ источник правды."""
    
    # Начальные состояния
    IDLE = "idle"                      # Начальное/неактивное
    CHECK_BOOKING = "check_booking"    # Проверка AmoCRM
    EXISTING_BOOKING = "existing"      # Показываем существующее бронирование
    
    # Основной flow
    ASK_DATE = "ask_date"              # Запрос даты
    ASK_KIDS = "ask_kids"              # Запрос количества детей
    CONFIRM_PHONE = "confirm_phone"    # Подтверждение телефона из CRM
    ASK_PHONE = "ask_phone"            # Ввод нового телефона
    ASK_FORMAT = "ask_format"          # Выбор формата (комната/ресторан)
    ASK_TIME = "ask_time"              # Выбор слота времени
    ASK_EXTRAS = "ask_extras"          # Предложение допуслуг
    COMPLETE = "complete"              # Flow завершён


@dataclass
class Transition:
    """Результат перехода состояния."""
    success: bool
    new_state: BirthdayState
    message: str = ""
    buttons: list = None
    error: str = None
    
    def __post_init__(self):
        if self.buttons is None:
            self.buttons = []


class BirthdayStateMachine:
    """
    State machine для birthday flow.
    
    Управляет состояниями и переходами между ними.
    Сохраняет состояние в БД (Lead.birthday_state).
    """
    
    # Маппинг валидных переходов: current_state -> [allowed_next_states]
    VALID_TRANSITIONS = {
        BirthdayState.IDLE: [BirthdayState.CHECK_BOOKING],
        BirthdayState.CHECK_BOOKING: [BirthdayState.EXISTING_BOOKING, BirthdayState.ASK_DATE],
        BirthdayState.EXISTING_BOOKING: [BirthdayState.IDLE, BirthdayState.ASK_DATE],  # Новая заявка
        BirthdayState.ASK_DATE: [BirthdayState.ASK_KIDS],
        BirthdayState.ASK_KIDS: [BirthdayState.CONFIRM_PHONE, BirthdayState.ASK_PHONE],
        BirthdayState.CONFIRM_PHONE: [BirthdayState.ASK_FORMAT, BirthdayState.ASK_PHONE],
        BirthdayState.ASK_PHONE: [BirthdayState.ASK_FORMAT],
        BirthdayState.ASK_FORMAT: [BirthdayState.ASK_TIME, BirthdayState.ASK_EXTRAS],  # Ресторан может пропустить time
        BirthdayState.ASK_TIME: [BirthdayState.ASK_EXTRAS],
        BirthdayState.ASK_EXTRAS: [BirthdayState.COMPLETE],
        BirthdayState.COMPLETE: [BirthdayState.IDLE],  # Reset
    }
    
    def __init__(self, user_id: str, platform: str = "telegram", park_id: str = "nn"):
        self.user_id = str(user_id)
        self.platform = platform
        self.park_id = park_id
        self._lead: Optional[Lead] = None
    
    def _get_lead(self, db) -> Optional[Lead]:
        """Получить активный лид пользователя."""
        from sqlalchemy import or_
        
        # Определяем поле ID в зависимости от платформы
        if self.platform == "telegram":
            filter_condition = Lead.telegram_id == self.user_id
        elif self.platform == "vk":
            filter_condition = Lead.vk_id == self.user_id
        else:
            filter_condition = Lead.web_session_id == self.user_id
        
        lead = db.query(Lead).filter(
            filter_condition,
            Lead.park_id == self.park_id,
            Lead.status.in_(["new", "contacted"]),
            or_(
                Lead.sent_to_manager == False,
                Lead.amocrm_deal_id != None
            )
        ).order_by(Lead.id.desc()).first()
        
        return lead
    
    def get_current_state(self) -> BirthdayState:
        """Получить текущее состояние из БД."""
        db = SessionLocal()
        try:
            lead = self._get_lead(db)
            if not lead:
                return BirthdayState.IDLE
            
            state_str = getattr(lead, 'birthday_state', None) or "idle"
            try:
                return BirthdayState(state_str)
            except ValueError:
                logger.warning(f"Unknown state '{state_str}', returning IDLE")
                return BirthdayState.IDLE
        finally:
            db.close()
    
    def set_state(self, new_state: BirthdayState, lead_id: int = None) -> bool:
        """Установить новое состояние в БД."""
        db = SessionLocal()
        try:
            if lead_id:
                lead = db.query(Lead).filter(Lead.id == lead_id).first()
            else:
                lead = self._get_lead(db)
            
            if not lead:
                logger.error(f"Cannot set state: no lead found for user {self.user_id}")
                return False
            
            lead.birthday_state = new_state.value
            db.commit()
            logger.info(f"Lead #{lead.id} state changed to {new_state.value}")
            return True
        except Exception as e:
            logger.error(f"Failed to set state: {e}")
            db.rollback()
            return False
        finally:
            db.close()
    
    def can_transition(self, from_state: BirthdayState, to_state: BirthdayState) -> bool:
        """Проверить, допустим ли переход."""
        allowed = self.VALID_TRANSITIONS.get(from_state, [])
        return to_state in allowed
    
    def transition(self, to_state: BirthdayState, lead_id: int = None) -> Transition:
        """
        Выполнить переход в новое состояние.
        
        Args:
            to_state: Целевое состояние
            lead_id: ID лида (опционально)
            
        Returns:
            Transition с результатом
        """
        current = self.get_current_state()
        
        # Проверяем допустимость перехода
        if not self.can_transition(current, to_state):
            logger.warning(f"Invalid transition: {current.value} -> {to_state.value}")
            # Разрешаем принудительный переход с логированием
            logger.info(f"Forcing transition anyway: {current.value} -> {to_state.value}")
        
        # Выполняем переход
        if self.set_state(to_state, lead_id):
            return Transition(
                success=True,
                new_state=to_state
            )
        else:
            return Transition(
                success=False,
                new_state=current,
                error="Failed to save state"
            )
    
    def reset(self) -> bool:
        """Сбросить состояние в IDLE."""
        return self.set_state(BirthdayState.IDLE)
    
    def get_collected_data(self) -> Dict[str, Any]:
        """Получить все собранные данные из лида."""
        db = SessionLocal()
        try:
            lead = self._get_lead(db)
            if not lead:
                return {}
            
            return {
                "lead_id": lead.id,
                "event_date": lead.event_date,
                "kids_count": lead.kids_count,
                "adults_count": lead.adults_count,
                "phone": lead.phone,
                "customer_name": lead.customer_name,
                "child_name": lead.child_name,
                "child_age": lead.child_age,
                "format": lead.format,
                "time": lead.time,
                "room": lead.room,
                "extras": lead.extras or [],
                "amocrm_deal_id": lead.amocrm_deal_id,
                "amocrm_contact_id": lead.amocrm_contact_id,
                "sent_to_manager": lead.sent_to_manager,
            }
        finally:
            db.close()
    
    def update_data(self, data: Dict[str, Any], lead_id: int = None) -> bool:
        """
        Обновить данные в лиде.
        
        Args:
            data: Словарь с данными для обновления
            lead_id: ID лида (опционально)
        """
        db = SessionLocal()
        try:
            if lead_id:
                lead = db.query(Lead).filter(Lead.id == lead_id).first()
            else:
                lead = self._get_lead(db)
            
            if not lead:
                logger.error(f"Cannot update data: no lead found for user {self.user_id}")
                return False
            
            # Обновляем поля
            for key, value in data.items():
                if hasattr(lead, key) and value is not None:
                    setattr(lead, key, value)
            
            db.commit()
            logger.info(f"Lead #{lead.id} updated: {list(data.keys())}")
            return True
        except Exception as e:
            logger.error(f"Failed to update data: {e}")
            db.rollback()
            return False
        finally:
            db.close()


def get_state_machine(user_id: str, platform: str = "telegram") -> BirthdayStateMachine:
    """Фабрика для создания state machine."""
    return BirthdayStateMachine(user_id, platform)
