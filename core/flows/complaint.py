"""
ComplaintFlow — обработка жалоб на обслуживание.
"""

import re
from core.flows.base import BaseFlow, FlowContext, FlowResult, FlowStatus
from core.notifications import needs_complaint_flow, format_complaint_message
from sqlalchemy.orm.attributes import flag_modified


class ComplaintFlow(BaseFlow):
    """
    Flow для обработки жалоб на обслуживание.
    
    Сценарий:
    1. Пользователь пишет жалобу (триггер)
    2. Если телефон известен — сразу отправляем менеджеру
    3. Если нет — запрашиваем телефон
    4. После получения телефона — отправляем менеджеру
    """
    
    def can_handle(self, context: FlowContext) -> bool:
        """Проверяем: либо уже в режиме жалобы, либо триггер жалобы."""
        # Уже в режиме жалобы
        if context.lead_data.get("complaint_step"):
            return True
        
        # Триггер новой жалобы
        if needs_complaint_flow(context.message_text):
            return True
        
        return False
    
    async def handle(self, context: FlowContext) -> FlowResult:
        """Обработка жалобы."""
        lead_data = context.lead_data
        complaint_step = lead_data.get("complaint_step")
        
        # ШАГ 2: Получаем телефон
        if complaint_step == "phone":
            return await self._handle_phone_step(context)
        
        # ШАГ 1: Начало жалобы (триггер)
        return await self._handle_trigger(context)
    
    async def _handle_trigger(self, context: FlowContext) -> FlowResult:
        """Обработка триггера жалобы."""
        # Проверяем, есть ли телефон в CRM
        phone = await self._get_phone_from_crm(context.user_id)
        
        if phone:
            # Телефон есть — сразу отправляем жалобу
            msg = format_complaint_message(
                platform=context.platform,
                user_id=context.user_id,
                user_name=context.display_name,
                complaint_text=context.message_text,
                phone=phone,
                username=context.username
            )
            
            return FlowResult.handled(
                text="😔 Нам очень жаль, что у вас остались негативные впечатления.\n\nИнформация передана руководству парка. Мы обязательно разберёмся в ситуации и свяжемся с вами в ближайшее время для решения вопроса.\n\nПриносим извинения за доставленные неудобства. 💚",
                should_notify_manager=True,
                manager_message=msg
            )
        else:
            # Телефона нет — запрашиваем
            return FlowResult.handled(
                text="😔 Нам очень жаль, что у вас остались негативные впечатления.\n\nМы обязательно разберёмся в ситуации!\n\n📱 Пожалуйста, оставьте ваш номер телефона — руководство парка свяжется с вами для решения вопроса.",
                update_session={
                    "intent": "complaint",
                    "lead_data": {
                        "complaint_step": "phone",
                        "complaint_text": context.message_text
                    }
                }
            )
    
    async def _handle_phone_step(self, context: FlowContext) -> FlowResult:
        """Обработка шага с телефоном."""
        phone_pattern = r'[\d\+\(\)\-\s]{7,}'
        
        if re.search(phone_pattern, context.message_text):
            # Телефон валидный — отправляем жалобу
            lead_data = context.lead_data
            
            msg = format_complaint_message(
                platform=context.platform,
                user_id=context.user_id,
                user_name=context.display_name,
                complaint_text=lead_data.get("complaint_text", ""),
                phone=context.message_text,
                username=context.username
            )
            
            return FlowResult.handled(
                text="🙏 Спасибо, что сообщили нам об этом!\n\nИнформация передана руководству парка. Мы обязательно разберёмся в ситуации и свяжемся с вами в ближайшее время для решения вопроса.\n\nПриносим извинения за доставленные неудобства. 💚",
                should_notify_manager=True,
                manager_message=msg,
                update_session={
                    "intent": "unknown",
                    "lead_data": {}
                }
            )
        else:
            # Телефон невалидный — просим снова
            return FlowResult.handled(
                text="📱 Пожалуйста, укажите корректный номер телефона:"
            )
    
    async def _get_phone_from_crm(self, user_id: str) -> str | None:
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
