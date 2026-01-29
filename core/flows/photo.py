"""
PhotoFlow — обработка запросов на фотографии.
"""

import re
from typing import Optional
from core.flows.base import BaseFlow, FlowContext, FlowResult
from core.notifications import needs_photo_request, format_photo_request_message, send_to_managers
from sqlalchemy.orm.attributes import flag_modified


class PhotoFlow(BaseFlow):
    """
    Flow для запросов фотографий.
    
    Сценарии:
    1. Запрос готовых фото с праздника
    2. Бронирование фотосессии
    """
    
    def can_handle(self, context: FlowContext) -> bool:
        """Проверяем: либо уже в режиме фото, либо триггер."""
        if context.lead_data.get("photo_step"):
            return True
        if context.intent == "photo_request":
            return True
        if needs_photo_request(context.message_text):
            return True
        return False
    
    async def handle(self, context: FlowContext) -> FlowResult:
        """Обработка запроса фото."""
        lead_data = context.lead_data
        photo_step = lead_data.get("photo_step")
        photo_type = lead_data.get("type", "request")
        
        # Шаг сбора телефона
        if photo_step == "phone":
            return await self._handle_phone_step(context)
        
        # Шаг сбора даты (для фотосессии)
        if photo_step == "date":
            return await self._handle_date_step(context)
        
        # Начало — определяем тип запроса
        return await self._start_flow(context)
    
    async def _start_flow(self, context: FlowContext) -> FlowResult:
        """Начало — проверяем телефон в CRM."""
        phone = await self._get_phone_from_crm(context.user_id)
        
        if phone:
            # Телефон есть — сразу отправляем уведомление
            msg = format_photo_request_message(
                platform=context.platform,
                user_id=context.user_id,
                user_name=context.display_name,
                phone=phone,
                description=context.message_text[:200],
                username=context.username
            )
            
            return FlowResult.handled(
                text="📷 Понимаю, что вы ждёте свои фотографии!\n\nМы передали ваш запрос, с вами свяжутся в ближайшее время. 💚",
                should_notify_manager=True,
                manager_message=msg
            )
        else:
            # Телефона нет — запрашиваем
            return FlowResult.handled(
                text="📷 Понимаю, что вы ждёте свои фотографии!\n\n📱 Оставьте ваш номер телефона, чтобы мы могли связаться с вами:",
                update_session={
                    "intent": "photo_request",
                    "lead_data": {"photo_step": "phone", "type": "request", "description": context.message_text[:200]}
                }
            )
    
    async def _handle_phone_step(self, context: FlowContext) -> FlowResult:
        """Обработка телефона."""
        lead_data = context.lead_data
        
        msg = format_photo_request_message(
            platform=context.platform,
            user_id=context.user_id,
            user_name=context.display_name,
            phone=context.message_text,
            description=lead_data.get("description", ""),
            username=context.username
        )
        
        return FlowResult.handled(
            text="📷 Спасибо! Мы передали ваш запрос, с вами свяжутся в ближайшее время. 💚",
            should_notify_manager=True,
            manager_message=msg,
            update_session={"intent": "unknown", "lead_data": {}}
        )
    
    async def _handle_date_step(self, context: FlowContext) -> FlowResult:
        """Обработка даты для фотосессии."""
        lead_data = context.lead_data.copy()
        lead_data["photo_date"] = context.message_text
        lead_data["photo_step"] = "phone"
        
        return FlowResult.handled(
            text="📸 Отлично! 📱 Укажите номер телефона для связи:",
            update_session={"lead_data": lead_data}
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
