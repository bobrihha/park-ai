"""Database models."""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, JSON, ForeignKey, Date
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class Session(Base):
    """Сессия пользователя."""
    __tablename__ = "sessions"
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(String(50), unique=True, index=True)
    username = Column(String(100))  # telegram username without @
    park_id = Column(String(10), default="nn")
    intent = Column(String(20), default="unknown")  # birthday, general, unknown
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Данные сбора лида
    lead_data = Column(JSON, default=dict)
    
    messages = relationship("Message", back_populates="session")


class Message(Base):
    """Сообщение в диалоге."""
    __tablename__ = "messages"
    
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("sessions.id"))
    role = Column(String(20))  # user, assistant
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    session = relationship("Session", back_populates="messages")


class Client(Base):
    """Карточка клиента."""
    __tablename__ = "clients"
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(String(50), unique=True, index=True)
    vk_id = Column(String(50), unique=True, index=True)  # New field
    username = Column(String(100))
    customer_name = Column(String(100))  # имя, которое сообщил клиент
    first_name = Column(String(100))
    last_name = Column(String(100))
    phone = Column(String(20))  # Основной телефон
    
    total_leads = Column(Integer, default=0)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    leads = relationship("Lead", back_populates="client")
    phones = relationship("ClientPhone", back_populates="client")
    children = relationship("ClientChild", back_populates="client")


class ClientPhone(Base):
    """Телефоны клиента."""
    __tablename__ = "client_phones"
    
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"))
    phone = Column(String(20))
    last_used_at = Column(DateTime, default=datetime.utcnow)
    
    client = relationship("Client", back_populates="phones")


class ClientChild(Base):
    """Дети клиента."""
    __tablename__ = "client_children"
    
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"))
    name = Column(String(100))
    event_date = Column(String(20))  # дата праздника из заявки
    birth_date = Column(Date)
    age = Column(Integer)
    
    client = relationship("Client", back_populates="children")


class Lead(Base):
    """Лид (заявка на праздник)."""
    __tablename__ = "leads"
    
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"))  # Ссылка на клиента
    telegram_id = Column(String(50), index=True)  # или vk_123456
    username = Column(String(100))  # telegram username
    park_id = Column(String(10), default="nn")
    source = Column(String(20), default="telegram")  # telegram, vk
    
    # Контактные данные
    customer_name = Column(String(100))
    phone = Column(String(20))
    
    # Данные праздника
    child_name = Column(String(100))
    child_age = Column(Integer)
    event_date = Column(String(20))  # строка пока так проще
    time = Column(String(20))
    
    # Детали
    kids_count = Column(Integer, default=0)
    adults_count = Column(Integer, default=0)
    format = Column(String(50))  # room_rent, turnkey, etc
    room = Column(String(50))
    extras = Column(Text)  # JSON or text list of extras
    
    status = Column(String(20), default="new")  # new, contacted, booked, cancelled
    notes = Column(Text)  # Комментарий менеджера
    
    # AmoCRM integration
    amocrm_deal_id = Column(String(50), index=True)  # ID сделки в AmoCRM
    amocrm_contact_id = Column(String(50))  # ID контакта в AmoCRM
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Флаги
    sent_to_manager = Column(Boolean, default=False)
    status_notified = Column(Boolean, default=False)  # Уведомлен ли клиент о смене статуса на «Взято в работу»
    
    # Birthday flow state machine
    birthday_state = Column(String(30), default="idle")  # idle, ask_date, ask_kids, etc.
    
    # VK support
    vk_id = Column(String(50), index=True)  # VK user ID
    web_session_id = Column(String(100), index=True)  # Web chat session ID
    
    client = relationship("Client", back_populates="leads")
    
    def get_summary(self):
        """Вернуть краткую информацию о заявке."""
        extras_text = ""
        if self.extras:
            # extras может быть JSON строкой или списком
            try:
                import json
                extras_list = json.loads(self.extras) if isinstance(self.extras, str) else self.extras
                if extras_list:
                    extras_text = f"\n🎁 Доп. услуги: {', '.join(extras_list)}"
            except:
                extras_text = f"\n🎁 Доп. услуги: {self.extras}"

        return (
            f"📋 <b>Новая заявка #{self.id}</b>\n"
            f"👤 Имя: {self.customer_name or 'Не указано'}\n"
            f"📞 Телефон: {self.phone or 'Не указан'}\n"
            f"👶 Именинник: {self.child_name or '-'} ({self.child_age or '?'} лет)\n"
            f"📅 Дата: {self.event_date or 'Не выбрана'} {self.time or ''}\n"
            f"📍 Формат: {self.format or 'Не выбран'}\n"
            f"👥 Гости: {self.kids_count} детей, {self.adults_count} взрослых"
            f"{extras_text}"
            f"\n💬 Комментарий: {self.notes or '-'}"
        )


class Document(Base):
    """Документ базы знаний."""
    __tablename__ = "documents"
    
    id = Column(Integer, primary_key=True)
    park_id = Column(String(10), default="nn")
    category = Column(String(20))  # general, birthday, shared
    title = Column(String(200))
    content = Column(Text)
    source_file = Column(String(500))
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BotCommand(Base):
    """Команда бота для быстрого меню."""
    __tablename__ = "bot_commands"
    
    id = Column(Integer, primary_key=True)
    command = Column(String(50), unique=True, index=True)  # prices, birthday, rules...
    title = Column(String(100))                             # 💰 Цены
    response = Column(Text)                                 # HTML-текст ответа
    is_active = Column(Boolean, default=True)
    has_logic = Column(Boolean, default=False)              # Если True - используется логика из handlers
    order = Column(Integer, default=0)                      # Порядок в меню
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Prompt(Base):
    """Системные промпты для AI-агента."""
    __tablename__ = "prompts"
    
    id = Column(Integer, primary_key=True)
    park_id = Column(String(10), default="nn", index=True)
    intent = Column(String(30), index=True)  # base, birthday, general, events, clarification
    name = Column(String(100))               # Название для админки
    content = Column(Text)                   # Текст промпта
    is_active = Column(Boolean, default=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

