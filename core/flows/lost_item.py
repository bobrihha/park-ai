"""
LostItemFlow — обработка потерянных вещей.
"""

import re
from typing import Optional
from core.flows.base import BaseFlow, FlowContext, FlowResult
from core.notifications import format_lost_item_message, needs_lost_item_flow, send_to_managers
from sqlalchemy.orm.attributes import flag_modified


class LostItemFlow(BaseFlow):
    """
    Flow для обработки потерянных вещей.
    
    Сценарий:
    1. Пользователь сообщает о потере (триггер)
    2. Спрашиваем дату посещения
    3. Спрашиваем место
    4. Спрашиваем описание вещи
    5. Спрашиваем телефон (или берём из CRM)
    6. Отправляем уведомление менеджеру
    """
    
    EXIT_KEYWORDS = [
        "ничего не потерял", "ничего не потеряла", "ничего не теряла", "ничего не терял",
        "не потерял", "не потеряла", "не теряла", "не терял",
        "я не про это", "я о другом", "хотел спросить", "хотела спросить",
        "я спрашиваю", "речь не об этом", "не об этом",
        "отмена", "стоп", "хватит", "выход", "exit", "cancel",
    ]
    
    def can_handle(self, context: FlowContext) -> bool:
        """Проверяем: либо уже в режиме потеряшек, либо триггер."""
        if context.lead_data.get("lost_step"):
            return True
        if context.intent == "lost_item":
            return True
        if needs_lost_item_flow(context.message_text):
            return True
        return False
    
    async def handle(self, context: FlowContext) -> FlowResult:
        """Обработка потеряшек."""
        lead_data = context.lead_data
        lost_step = lead_data.get("lost_step")
        message_lower = context.message_text.lower()
        
        # Проверяем выход из опроса
        if lost_step and any(kw in message_lower for kw in self.EXIT_KEYWORDS):
            return FlowResult.handled(
                text="Ой, простите за недопонимание! 😊\n\nЧем могу помочь? Спрашивайте — я отвечу на любые вопросы о парке, ценах или празднике! 💚",
                update_session={"intent": "unknown", "lead_data": {}}
            )
        
        # Обработка по шагам
        if lost_step == "confirm":
            # Ожидаем callback, но если пришёл текст — проверяем
            if any(x in message_lower for x in ["да", "потерял", "потеряла", "найти"]):
                return await self._start_survey(context)
            elif any(x in message_lower for x in ["нет", "другой", "другое", "не потерял"]):
                return FlowResult.handled(
                    text="Понял! 😊 Тогда чем могу помочь? Спрашивайте о парке, ценах или празднике! 💚",
                    update_session={"intent": "unknown", "lead_data": {}}
                )
            # Если непонятно — переспрашиваем
            return FlowResult.handled(
                text="Вы потеряли вещь в нашем парке? 🔍",
                buttons=[
                    {"text": "✅ Да, помогите найти", "callback": "lost_confirm_yes"},
                    {"text": "❌ Нет, другой вопрос", "callback": "lost_confirm_no"}
                ]
            )
        elif lost_step == "date":
            return await self._handle_date_step(context)
        elif lost_step == "location":
            return await self._handle_location_step(context)
        elif lost_step == "description":
            return await self._handle_description_step(context)
        elif lost_step == "phone":
            return await self._handle_phone_step(context)
        elif lost_step == "confirm_phone":
            return await self._handle_confirm_phone_step(context)
        
        # Первый триггер — спрашиваем подтверждение
        return await self._ask_confirmation(context)
    
    async def _ask_confirmation(self, context: FlowContext) -> FlowResult:
        """Спрашиваем подтверждение перед началом опроса."""
        return FlowResult.handled(
            text="Вы потеряли вещь в нашем парке? 🔍\n\nЕсли да — помогу передать информацию в бюро находок!",
            buttons=[
                {"text": "✅ Да, помогите найти", "callback": "lost_confirm_yes"},
                {"text": "❌ Нет, другой вопрос", "callback": "lost_confirm_no"}
            ],
            update_session={
                "intent": "lost_item",
                "lead_data": {
                    "lost_step": "confirm",
                    "original_message": context.message_text  # Сохраняем для обработки при "Нет"
                }
            }
        )
    
    async def _start_survey(self, context: FlowContext) -> FlowResult:
        """Начало опроса о потере (после подтверждения)."""
        return FlowResult.handled(
            text="Ой, как жаль! 😔 Давайте попробуем найти вашу вещь.\n\n📅 Когда вы были в парке? (напишите дату)",
            update_session={
                "intent": "lost_item",
                "lead_data": {"lost_step": "date"}
            }
        )
    
    async def _handle_date_step(self, context: FlowContext) -> FlowResult:
        """Шаг сбора даты."""
        lead_data = context.lead_data.copy()
        lead_data["lost_date"] = context.message_text
        lead_data["lost_step"] = "location"
        
        return FlowResult.handled(
            text="📍 В каком примерно месте вы могли оставить вещь?\n(аттракцион, комната, ресторан и т.д.)",
            update_session={"lead_data": lead_data}
        )
    
    async def _handle_location_step(self, context: FlowContext) -> FlowResult:
        """Шаг сбора места."""
        lead_data = context.lead_data.copy()
        lead_data["lost_location"] = context.message_text
        lead_data["lost_step"] = "description"
        
        return FlowResult.handled(
            text="🔍 Опишите, что именно потеряли?\n(цвет, размер, особенности)",
            update_session={"lead_data": lead_data}
        )
    
    async def _handle_description_step(self, context: FlowContext) -> FlowResult:
        """Шаг сбора описания."""
        lead_data = context.lead_data.copy()
        lead_data["lost_description"] = context.message_text
        
        # Проверяем телефон в CRM
        phone = await self._get_phone_from_crm(context.user_id)
        
        if phone:
            lead_data["phone"] = phone
            lead_data["lost_step"] = "confirm_phone"
            
            return FlowResult.handled(
                text=f"📱 Для связи использовать номер {phone}?",
                buttons=[
                    {"text": "✅ Да", "callback": "lost_phone_yes"},
                    {"text": "❌ Другой", "callback": "lost_phone_no"}
                ],
                update_session={"lead_data": lead_data}
            )
        else:
            lead_data["lost_step"] = "phone"
            return FlowResult.handled(
                text="📱 Укажите номер телефона для связи:",
                update_session={"lead_data": lead_data}
            )
    
    async def _handle_phone_step(self, context: FlowContext) -> FlowResult:
        """Шаг сбора телефона."""
        lead_data = context.lead_data
        
        msg = format_lost_item_message(
            platform=context.platform,
            user_id=context.user_id,
            user_name=context.display_name,
            lost_date=lead_data.get("lost_date"),
            lost_location=lead_data.get("lost_location"),
            lost_description=lead_data.get("lost_description"),
            phone=context.message_text,
            username=context.username
        )
        
        return FlowResult.handled(
            text="✅ Спасибо! Мы передали информацию в бюро находок.\n\nМенеджер свяжется с вами, если вещь найдётся. 💚",
            should_notify_manager=True,
            manager_message=msg,
            update_session={"intent": "unknown", "lead_data": {}}
        )
    
    async def _handle_confirm_phone_step(self, context: FlowContext) -> FlowResult:
        """Обработка ответа на подтверждение телефона (из callback)."""
        # Обычно обрабатывается через callback, но на всякий случай
        return FlowResult.not_applicable()
    
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
