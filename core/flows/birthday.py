"""
BirthdayFlow — основной flow бронирования дня рождения.

Шаги:
1. Дата мероприятия
2. Количество детей
3. Телефон (с проверкой AmoCRM + кнопки подтверждения)
4. Формат (комната/ресторан) — КНОПКИ
5. Слот времени — КНОПКИ
6. Подтверждение имени — КНОПКИ
7. Итог + предложение допуслуг — КНОПКИ каталогов
"""

import re
import logging
from datetime import datetime
from typing import Optional

from core.flows.base import BaseFlow, FlowContext, FlowResult, FlowStatus
from core.utils import (
    parse_user_date,
    format_date_ru,
    build_birthday_date_question,
    parse_kids_count,
    extract_phone_from_message,
    format_birthday_price_options,
    calculate_birthday_price,
)
from core.lead_service import (
    get_or_create_lead,
    update_lead_from_data,
    lead_to_dict,
    save_amocrm_deal_id,
    save_amocrm_contact_id,
    mark_lead_sent_to_manager,
)
from core.amocrm import send_lead_to_amocrm
from core.notifications import send_to_birthday_channel, format_lead_message
from sqlalchemy.orm.attributes import flag_modified

logger = logging.getLogger(__name__)


class BirthdayFlow(BaseFlow):
    """
    Flow бронирования дня рождения с кнопками.
    """
    
    BIRTHDAY_TRIGGERS = [
        "день рождения", "днюха", "днюху", "др ", " др", "д.р.",
        "birthday", "бёздей", "бездей",
        "забронировать праздник", "бронь на праздник", "отпраздновать",
        "хочу праздник", "планирую праздник", "праздник ребёнк", "праздник ребенк",
        "забронировать комнату", "бронь комнаты", "бронирование комнаты",
        "забронировать зону", "снять комнату",
        "именинник", "имениннику", "ребёнку исполняется", "ребенку исполняется",
        "хочу забронировать", "можно забронировать", "как забронировать",
    ]
    
    def __init__(self, db_session=None, agent=None):
        super().__init__(db_session)
        self.agent = agent
    
    def can_handle(self, context: FlowContext) -> bool:
        """Birthday flow активен когда intent = birthday / birthday_additional ИЛИ есть триггер."""
        if context.intent in ("birthday", "birthday_additional"):
            logger.info(f"BirthdayFlow.can_handle: intent={context.intent}, returning True")
            return True
        
        message_lower = context.message_text.lower()
        for trigger in self.BIRTHDAY_TRIGGERS:
            if trigger in message_lower:
                logger.info(f"BirthdayFlow.can_handle: trigger '{trigger}' found, returning True")
                return True
        
        return False
    
    async def handle(self, context: FlowContext) -> FlowResult:
        """Основная обработка birthday flow."""
        lead_data = context.lead_data or {}
        
        # Первое сообщение с триггером — устанавливаем intent и приветствуем
        if context.intent not in ("birthday", "birthday_additional"):
            return FlowResult.handled(
                text="🎉 Отлично, давайте забронируем праздник!\n\n📅 На какую дату планируете мероприятие?",
                update_session={"intent": "birthday", "lead_data": {}}
            )
        
        # Получаем или создаём Lead
        lead = get_or_create_lead(
            context.user_id,
            source=context.platform,
            park_id=context.park_id,
            username=context.username,
            first_name=context.first_name
        )
        
        current_data = lead_to_dict(lead)
        message = context.message_text
        
        # ==================== ШАГ 1: ДАТА ====================
        if not current_data.get("event_date"):
            parsed_date = parse_user_date(message)
            if parsed_date:
                normalized_date = format_date_ru(parsed_date, include_year=False)
                update_lead_from_data(lead.id, {"event_date": normalized_date})
                
                response = build_birthday_date_question(parsed_date)
                return FlowResult.handled(
                    text=response,
                    update_session={"lead_data": {**lead_data, "step": "kids"}}
                )
            # Дата не распознана — AI ответит и вернёт к вопросу
            return FlowResult.defer_to_ai(
                pending_step="date",
                pending_prompt="📅 На какую дату планируете праздник?"
            )
        
        # ==================== ШАГ 2: КОЛИЧЕСТВО ДЕТЕЙ ====================
        if not current_data.get("kids_count"):
            kids_count = parse_kids_count(message)
            if kids_count:
                update_lead_from_data(lead.id, {"kids_count": kids_count})
                
                # Проверяем телефон в AmoCRM
                phone = await self._get_phone_from_crm(context.user_id, context.platform)
                
                if phone:
                    # Нашли телефон — предлагаем подтвердить
                    return FlowResult.handled(
                        text=f"👶 Отлично, {kids_count} детей!\n\n📱 Использовать этот номер для связи?\n\n{phone}",
                        buttons=[
                            {"text": f"✅ Да, {phone}", "callback": "phone_confirm_yes"},
                            {"text": "📱 Другой номер", "callback": "phone_confirm_no"}
                        ],
                        update_session={"lead_data": {**lead_data, "step": "phone", "pending_phone": phone}}
                    )
                else:
                    # Телефон не найден — просим ввести
                    return FlowResult.handled(
                        text=f"👶 Отлично, {kids_count} детей!\n\n📱 Укажите номер телефона для связи:",
                        update_session={"lead_data": {**lead_data, "step": "phone_input"}}
                    )
            # Количество детей не распознано — AI ответит и вернёт к вопросу
            return FlowResult.defer_to_ai(
                pending_step="kids",
                pending_prompt="👶 Сколько детей будет всего, включая именинника?"
            )
        
        # ==================== ШАГ 3: ТЕЛЕФОН (ввод вручную) ====================
        step = lead_data.get("step", "")
        
        if step == "phone_input" and not current_data.get("phone"):
            phone = extract_phone_from_message(message)
            if phone:
                update_lead_from_data(lead.id, {"phone": phone})
                
                # Отправляем в AmoCRM
                await self._send_to_crm(context, lead, {**current_data, "phone": phone})
                
                # Переходим к выбору формата
                return await self._show_format_choice(context, lead, current_data, lead_data)
            # Телефон не распознан — AI ответит и вернёт к вопросу
            return FlowResult.defer_to_ai(
                pending_step="phone",
                pending_prompt="📱 Укажите номер телефона для связи:"
            )
        
        # ==================== ШАГ 4: ФОРМАТ (ждём callback, но если текст — обрабатываем) ====================
        if step == "format" or (current_data.get("phone") and not current_data.get("format")):
            format_text = message.lower()
            if "комнат" in format_text or "room" in format_text:
                update_lead_from_data(lead.id, {"format": "комната"})
                return await self._show_time_slots(context, lead, current_data, lead_data)
            elif "рестора" in format_text or "зона" in format_text or "столик" in format_text:
                update_lead_from_data(lead.id, {"format": "ресторан"})
                return await self._show_time_slots(context, lead, current_data, lead_data)
            # Если непонятный ответ — показываем формат ещё раз
            return await self._show_format_choice(context, lead, current_data, lead_data)
        
        # ==================== ШАГ 5: ВРЕМЯ ====================
        if step == "time" or (current_data.get("format") and not current_data.get("time")):
            # Пробуем распарсить время из текста
            time_match = re.search(r'(\d{1,2})[:\.]?(\d{2})?', message)
            if time_match:
                hour = int(time_match.group(1))
                minute = time_match.group(2) or "00"
                time_str = f"{hour}:{minute}"
                update_lead_from_data(lead.id, {"time": time_str})
                
                # Переходим к подтверждению имени
                return await self._show_name_confirmation(context, lead, current_data, lead_data)
            return FlowResult.not_applicable()
        
        # ==================== ШАГ 6: ИМЯ ====================
        if step == "name" or (current_data.get("time") and not current_data.get("customer_name")):
            # Сохраняем имя
            if len(message.strip()) > 1:
                update_lead_from_data(lead.id, {"customer_name": message.strip()})
                return await self._show_final_summary(context, lead, current_data, lead_data)
            return FlowResult.not_applicable()
        
        # Всё собрано — передаём AI
        return FlowResult.not_applicable()
    
    async def _show_format_choice(self, context, lead, current_data, lead_data) -> FlowResult:
        """Показать выбор формата с расчётом цен и КНОПКАМИ."""
        event_date = parse_user_date(current_data.get("event_date", ""))
        kids_count = current_data.get("kids_count", 0)
        
        if event_date and kids_count:
            room_calc = calculate_birthday_price(event_date, kids_count, "room", context.park_id)
            zone_calc = calculate_birthday_price(event_date, kids_count, "zone", context.park_id)
            
            text = (
                f"📞 Отлично, записал!\n\n"
                f"Давайте выберем формат праздника! 💚\n\n"
                f"🏠 Тематическая комната — 3 часа\n"
                f"Для {kids_count} детей: {room_calc['description']}\n\n"
                f"🍰 Столик в ресторане — без ограничений по времени\n"
                f"Для {kids_count} детей: {zone_calc['description']}\n\n"
                f"Какой формат вам ближе?"
            )
        else:
            text = "📞 Отлично, записал!\n\nКакой формат праздника предпочитаете?"
        
        return FlowResult.handled(
            text=text,
            buttons=[
                {"text": "🏠 Комната", "callback": "format_room"},
                {"text": "🍰 Ресторан", "callback": "format_zone"}
            ],
            update_session={"lead_data": {**lead_data, "step": "format"}}
        )
    
    async def _show_time_slots(self, context, lead, current_data, lead_data) -> FlowResult:
        """Показать выбор слотов времени КНОПКАМИ."""
        format_name = current_data.get("format", "комната")
        format_emoji = "🏠" if format_name == "комната" else "🍰"
        
        return FlowResult.handled(
            text=f"{format_emoji} Отлично, выбрали: {format_name}!\n\nКакой слот времени удобен?",
            buttons=[
                {"text": "🕙 10:30", "callback": "time_1030"},
                {"text": "🕝 14:30", "callback": "time_1430"},
                {"text": "🕡 18:30", "callback": "time_1830"}
            ],
            update_session={"lead_data": {**lead_data, "step": "time"}}
        )
    
    async def _show_name_confirmation(self, context, lead, current_data, lead_data) -> FlowResult:
        """Показать подтверждение имени из соцсети."""
        name = context.first_name or "Клиент"
        
        return FlowResult.handled(
            text=f"⏰ Время записал!\n\nВаше имя {name}? Оставить или изменить?",
            buttons=[
                {"text": f"✅ Да, {name}", "callback": "name_confirm_yes"},
                {"text": "✏️ Изменить", "callback": "name_confirm_no"}
            ],
            update_session={"lead_data": {**lead_data, "step": "name", "pending_name": name}}
        )
    
    async def _show_final_summary(self, context, lead, current_data, lead_data) -> FlowResult:
        """Показать итог + предложение допуслуг."""
        # Обновляем current_data
        lead = get_or_create_lead(context.user_id)
        current_data = lead_to_dict(lead)
        
        # Отправляем в AmoCRM если ещё не отправлено
        if not lead.sent_to_manager:
            await self._send_to_crm(context, lead, current_data)
        
        text = (
            f"✅ Заявка принята! Менеджер скоро свяжется.\n\n"
            f"📋 Ваша бронь:\n"
            f"- Дата: {current_data.get('event_date', '—')}\n"
            f"- Детей: {current_data.get('kids_count', '—')}\n"
            f"- Формат: {current_data.get('format', '—')}\n"
            f"- Время: {current_data.get('time', '—')}\n\n"
            f"💡 Пока ждёте звонка, можете посмотреть:"
        )
        
        return FlowResult.handled(
            text=text,
            buttons=[
                {"text": "🎂 Каталог тортов", "callback": "view_cakes"},
                {"text": "🎭 Аниматоры", "callback": "view_animators"}
            ],
            update_session={"lead_data": {**lead_data, "step": "done"}}
        )
    
    async def _send_to_crm(self, context, lead, lead_data: dict):
        """Отправка лида в AmoCRM и уведомление менеджера."""
        try:
            lead_dict = lead_data.copy()
            lead_dict["source"] = context.platform
            
            amocrm_deal_id, amocrm_contact_id = await send_lead_to_amocrm(
                lead_dict,
                telegram_id=context.user_id if context.platform == "telegram" else None,
                vk_id=context.user_id if context.platform == "vk" else None,
                username=context.username
            )
            
            if amocrm_deal_id:
                save_amocrm_deal_id(lead.id, str(amocrm_deal_id))
                if amocrm_contact_id:
                    save_amocrm_contact_id(lead.id, str(amocrm_contact_id))
                logger.info(f"Lead #{lead.id} sent to AmoCRM, deal_id={amocrm_deal_id}")
            
            # Уведомляем менеджера
            msg = format_lead_message(
                platform=context.platform,
                user_id=context.user_id,
                lead_data=lead_dict,
                username=context.username
            )
            await send_to_birthday_channel(msg)
            mark_lead_sent_to_manager(lead.id)
            logger.info(f"Lead #{lead.id} notification sent to managers")
                
        except Exception as e:
            logger.error(f"Failed to send to CRM/managers: {e}")
    
    async def _get_phone_from_crm(self, user_id: str, platform: str) -> Optional[str]:
        """Получить телефон из AmoCRM."""
        try:
            from core.amocrm import amocrm_client
            
            if platform == "telegram":
                contact = await amocrm_client.find_contact_by_telegram_id(int(user_id))
            elif platform == "vk":
                contact = await amocrm_client.find_contact_by_vk_id(int(user_id))
            else:
                return None
            
            if contact:
                contact_info = amocrm_client.get_contact_info(contact)
                return contact_info.get("phone")
        except Exception as e:
            logger.error(f"Failed to get phone from CRM: {e}")
        return None
