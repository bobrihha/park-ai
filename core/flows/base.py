"""
BaseFlow — базовый класс для всех flow.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Any
from enum import Enum


class FlowStatus(Enum):
    """Статус обработки flow."""
    HANDLED = "handled"      # Flow обработал сообщение
    NOT_APPLICABLE = "not_applicable"  # Flow не применим
    CONTINUE = "continue"    # Передать следующему flow
    DEFER_TO_AI = "defer_to_ai"  # Пусть AI ответит, потом вернуть к шагу


@dataclass
class FlowResult:
    """Результат обработки flow."""
    status: FlowStatus
    text: Optional[str] = None
    buttons: List[dict] = field(default_factory=list)
    image_path: Optional[str] = None
    
    # Уведомления
    should_notify_manager: bool = False
    manager_message: Optional[str] = None
    manager_channel: str = "general"  # "general" | "birthday"
    
    # Данные
    update_session: Optional[dict] = None  # {"intent": "...", "lead_data": {...}}
    lead_data: Optional[dict] = None  # Для AmoCRM
    
    # Для DEFER_TO_AI — возврат к шагу после ответа AI
    pending_step: Optional[str] = None  # Название шага (date, kids, phone, format, time)
    pending_prompt: Optional[str] = None  # Вопрос для возврата к шагу
    
    @classmethod
    def not_applicable(cls) -> "FlowResult":
        """Flow не применим к этому сообщению."""
        return cls(status=FlowStatus.NOT_APPLICABLE)
    
    @classmethod
    def handled(cls, text: str, **kwargs) -> "FlowResult":
        """Flow обработал сообщение."""
        return cls(status=FlowStatus.HANDLED, text=text, **kwargs)
    
    @classmethod
    def defer_to_ai(cls, pending_step: str, pending_prompt: str) -> "FlowResult":
        """Передать AI, но после ответа вернуть к текущему шагу."""
        return cls(
            status=FlowStatus.DEFER_TO_AI,
            pending_step=pending_step,
            pending_prompt=pending_prompt
        )


@dataclass
class FlowContext:
    """Контекст для обработки сообщения."""
    user_id: str
    platform: str  # "telegram" | "vk" | "web"
    username: Optional[str]
    first_name: Optional[str]
    message_text: str
    
    # Данные сессии
    session_id: int
    intent: str
    lead_data: dict
    
    # Дополнительно
    park_id: str = "nn"
    
    @property
    def display_name(self) -> str:
        if self.first_name:
            return self.first_name
        if self.username:
            return f"@{self.username}"
        return "Гость"


class BaseFlow(ABC):
    """
    Базовый класс для flow.
    
    Каждый flow должен реализовать:
    - can_handle() - проверка применимости
    - handle() - обработка сообщения
    """
    
    def __init__(self, db_session=None):
        self.db = db_session
    
    @abstractmethod
    def can_handle(self, context: FlowContext) -> bool:
        """
        Проверить, может ли этот flow обработать сообщение.
        Вызывается перед handle().
        """
        pass
    
    @abstractmethod
    async def handle(self, context: FlowContext) -> FlowResult:
        """
        Обработать сообщение.
        Вызывается только если can_handle() вернул True.
        """
        pass
    
    async def process(self, context: FlowContext) -> FlowResult:
        """
        Публичный метод — проверяет применимость и обрабатывает.
        """
        if not self.can_handle(context):
            return FlowResult.not_applicable()
        return await self.handle(context)
