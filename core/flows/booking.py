"""
BookingFlow — показ и изменение бронирований.
"""

import re
from typing import Optional
from core.flows.base import BaseFlow, FlowContext, FlowResult
from core.notifications import (
    needs_booking_change_request, 
    get_booking_change_type,
    format_booking_change_message,
    send_to_birthday_channel
)
from core.lead_service import lead_to_dict
from db import SessionLocal, Lead


class BookingQueryFlow(BaseFlow):
    """
    Flow для показа информации о бронировании.
    
    Триггеры: "моя бронь", "статус заявки", "проверить бронь" и т.д.
    """
    
    BOOKING_KEYWORDS = [
        "моя бронь", "моё бронь", "мое бронь", "мои брони",
        "моя заявка", "моё заявка", "мое заявка", "мои заявки",
        "моё бронирование", "мое бронирование", "мои бронирования",
        "статус заявки", "статус брони", "статус бронирования",
        "мой праздник", "моё праздник", "мое праздник",
        "что с заявкой", "что с бронью", "что с бронированием",
        "где моя заявка", "где моя бронь",
        "проверить бронь", "посмотреть бронь", "узнать статус"
    ]
    
    def can_handle(self, context: FlowContext) -> bool:
        """Проверяем триггер."""
        message_lower = context.message_text.lower()
        return any(kw in message_lower for kw in self.BOOKING_KEYWORDS)
    
    async def handle(self, context: FlowContext) -> FlowResult:
        """Показать бронирования пользователя."""
        db = SessionLocal()
        try:
            leads = db.query(Lead).filter(
                Lead.telegram_id == context.user_id,
                Lead.status.in_(["new", "contacted", "booked"]),
                Lead.sent_to_manager == True
            ).order_by(Lead.created_at.desc()).limit(3).all()
            
            if leads:
                # Форматируем информацию о бронях
                texts = []
                buttons = []
                
                for lead in leads:
                    text = self._format_booking_info(lead)
                    texts.append(text)
                    buttons.extend([
                        {"text": "✏️ Изменить дату/время", "callback": f"change_{lead.id}_datetime"},
                        {"text": "👥 Изменить кол-во гостей", "callback": f"change_{lead.id}_guests"},
                        {"text": "🎁 Добавить услуги", "callback": f"change_{lead.id}_extras"},
                        {"text": "❌ Отменить бронь", "callback": f"change_{lead.id}_cancel"}
                    ])
                
                return FlowResult.handled(
                    text="\n\n---\n\n".join(texts),
                    buttons=buttons[:4]  # Показываем кнопки для первой брони
                )
            else:
                return FlowResult.handled(
                    text="📋 У вас пока нет активных бронирований.\n\nХотите забронировать праздник? Напишите /birthday 🎉"
                )
        finally:
            db.close()
    
    def _format_booking_info(self, lead) -> str:
        """Форматировать информацию о бронировании."""
        data = lead_to_dict(lead)
        
        status_map = {
            "new": "🆕 Новая заявка",
            "contacted": "📞 Связались с вами",
            "booked": "✅ Подтверждено",
            "completed": "🎉 Завершено",
            "cancelled": "❌ Отменено"
        }
        
        lines = [f"📋 <b>Заявка #{lead.id}</b>"]
        lines.append(f"Статус: {status_map.get(lead.status, lead.status)}")
        
        if data.get("event_date"):
            lines.append(f"📅 Дата: {data['event_date']}")
        if data.get("kids_count"):
            lines.append(f"👶 Детей: {data['kids_count']}")
        if data.get("format"):
            lines.append(f"🎈 Формат: {data['format']}")
        if data.get("extras"):
            lines.append(f"🎁 Доп. услуги: {', '.join(data['extras'])}")
        
        return "\n".join(lines)


class BookingChangeFlow(BaseFlow):
    """
    Flow для запросов на изменение бронирования.
    
    Триггеры: "изменить дату", "перенести", "отменить бронь" и т.д.
    """
    
    def can_handle(self, context: FlowContext) -> bool:
        """Проверяем триггер."""
        return needs_booking_change_request(context.message_text)
    
    async def handle(self, context: FlowContext) -> FlowResult:
        """Обработка запроса на изменение."""
        change_type = get_booking_change_type(context.message_text)
        
        # Ищем телефон в CRM
        phone = await self._get_phone_from_crm(context.user_id)
        deal_id = await self._get_deal_id(context.user_id)
        
        msg = format_booking_change_message(
            platform=context.platform,
            user_id=context.user_id,
            user_name=context.display_name,
            change_type=change_type,
            message_text=context.message_text,
            deal_id=deal_id,
            phone=phone,
            username=context.username
        )
        
        return FlowResult.handled(
            text=f"✅ Ваш запрос на «{change_type}» передан менеджеру!\n\nМы свяжемся с вами в ближайшее время для уточнения деталей. 📞",
            should_notify_manager=True,
            manager_message=msg,
            manager_channel="birthday"
        )
    
    async def _get_phone_from_crm(self, user_id: str) -> Optional[str]:
        """Получить телефон из AmoCRM."""
        try:
            from core.amocrm import amocrm_client
            contact = await amocrm_client.find_contact_by_telegram_id(int(user_id))
            if contact:
                contact_info = amocrm_client.get_contact_info(contact)
                return contact_info.get("phone")
        except Exception:
            pass
        return None
    
    async def _get_deal_id(self, user_id: str) -> Optional[str]:
        """Получить последнюю сделку из AmoCRM."""
        try:
            from core.amocrm import amocrm_client
            contact = await amocrm_client.find_contact_by_telegram_id(int(user_id))
            if contact:
                deals = await amocrm_client.get_contact_deals(contact["id"])
                if deals:
                    return str(deals[0].get("id", ""))
        except Exception:
            pass
        return None
