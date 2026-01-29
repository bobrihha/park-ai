"""
PartnershipFlow — обработка предложений о сотрудничестве.
"""

import re
from typing import Optional
from core.flows.base import BaseFlow, FlowContext, FlowResult
from core.notifications import needs_partnership_proposal, format_partnership_message, send_to_managers


class PartnershipFlow(BaseFlow):
    """
    Flow для предложений о сотрудничестве.
    
    Сценарий:
    1. Пользователь предлагает сотрудничество (триггер)
    2. Спрашиваем суть предложения
    3. Спрашиваем телефон
    4. Отправляем уведомление менеджеру
    """
    
    def can_handle(self, context: FlowContext) -> bool:
        """Проверяем: либо уже в режиме партнёрства, либо триггер."""
        if context.lead_data.get("partnership_step"):
            return True
        if context.intent == "partnership":
            return True
        if needs_partnership_proposal(context.message_text):
            return True
        return False
    
    async def handle(self, context: FlowContext) -> FlowResult:
        """Обработка предложения."""
        lead_data = context.lead_data
        step = lead_data.get("partnership_step")
        
        if step == "details":
            return await self._handle_details_step(context)
        elif step == "phone":
            return await self._handle_phone_step(context)
        
        # Начало
        return await self._start_flow(context)
    
    async def _start_flow(self, context: FlowContext) -> FlowResult:
        """Начало опроса."""
        return FlowResult.handled(
            text="🤝 Здорово, что вы хотите сотрудничать с нами!\n\n📝 Расскажите, пожалуйста, подробнее о вашем предложении — в чём его суть?",
            update_session={
                "intent": "partnership",
                "lead_data": {"partnership_step": "details"}
            }
        )
    
    async def _handle_details_step(self, context: FlowContext) -> FlowResult:
        """Получили суть предложения — запрашиваем телефон."""
        return FlowResult.handled(
            text="📝 Отлично, записал!\n\n📱 Оставьте, пожалуйста, ваш номер телефона для связи:",
            update_session={
                "lead_data": {
                    "partnership_step": "phone",
                    "proposal_text": context.message_text[:500]
                }
            }
        )
    
    async def _handle_phone_step(self, context: FlowContext) -> FlowResult:
        """Получили телефон — отправляем уведомление."""
        lead_data = context.lead_data
        
        phone_pattern = r'[\d\+\(\)\-\s]{7,}'
        if not re.search(phone_pattern, context.message_text):
            return FlowResult.handled(
                text="📱 Пожалуйста, укажите корректный номер телефона:"
            )
        
        msg = format_partnership_message(
            platform=context.platform,
            user_id=context.user_id,
            user_name=context.display_name,
            proposal_text=lead_data.get("proposal_text", ""),
            phone=context.message_text,
            username=context.username
        )
        
        return FlowResult.handled(
            text="🤝 Спасибо за ваше предложение!\n\nМы передали его руководству. С вами свяжутся в ближайшее время! 💚",
            should_notify_manager=True,
            manager_message=msg,
            update_session={"intent": "unknown", "lead_data": {}}
        )
