"""
BirthdayFlowHandler — основной обработчик birthday flow.

Делегирует обработку в StateMachine и BookingChecker.
Поддерживает Telegram, VK и Web.
"""

import logging
import re
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass

from core.birthday.state_machine import BirthdayStateMachine, BirthdayState, get_state_machine
from core.birthday.booking_checker import BookingChecker, BookingStatus
from core.birthday.messages import MESSAGES, contains_birthday_trigger

from core.utils import (
    parse_user_date,
    format_date_ru,
    parse_kids_count,
    extract_phone_from_message,
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
from core.amocrm import send_lead_to_amocrm, amocrm_client
from core.notifications import send_to_birthday_channel, format_lead_message

from db import SessionLocal, Lead

logger = logging.getLogger(__name__)


@dataclass
class FlowResponse:
    """Ответ birthday flow."""
    text: str
    buttons: list = None
    parse_mode: str = "HTML"
    image_path: str = None
    update_state: BirthdayState = None
    
    def __post_init__(self):
        if self.buttons is None:
            self.buttons = []


class BirthdayFlowHandler:
    """
    Главный обработчик birthday flow.
    
    Использует:
    - StateMachine для управления состояниями
    - BookingChecker для проверки AmoCRM
    - Messages для текстов
    """
    
    def __init__(self):
        self.agent = None  # Будет установлен при необходимости
    
    def set_agent(self, agent):
        """Установить AI агента для обработки вопросов."""
        self.agent = agent
    
    async def handle_trigger(
        self,
        user_id: str,
        platform: str = "telegram",
        username: str = None,
        first_name: str = None,
        park_id: str = "nn"
    ) -> FlowResponse:
        """
        Обработать триггер входа в birthday flow.
        
        Вызывается при:
        - Нажатии кнопки "Организовать праздник"
        - Команде /birthday
        - Триггерных словах в сообщении
        """
        logger.info(f"Birthday trigger for user {user_id} ({platform})")
        
        # 1. Проверяем существующее бронирование в AmoCRM
        checker = BookingChecker(user_id, platform, park_id)
        booking_status = await checker.check_existing_booking()
        
        if booking_status.has_booking:
            # Есть активное бронирование — показываем его
            return await self._show_existing_booking(
                user_id, platform, booking_status, park_id
            )
        
        # 2. Нет бронирования — начинаем flow
        # Создаём/получаем лид
        lead = get_or_create_lead(
            user_id,
            source=platform,
            park_id=park_id,
            username=username,
            first_name=first_name
        )
        
        # Устанавливаем начальное состояние
        sm = get_state_machine(user_id, platform)
        sm.set_state(BirthdayState.ASK_DATE, lead.id)
        
        # Возвращаем приветствие
        return FlowResponse(
            text=MESSAGES["welcome"],
            update_state=BirthdayState.ASK_DATE
        )
    
    async def _show_existing_booking(
        self,
        user_id: str,
        platform: str,
        booking: BookingStatus,
        park_id: str
    ) -> FlowResponse:
        """Показать существующее бронирование с кнопками действий."""
        data = booking.booking_data
        
        text = MESSAGES["existing_booking"].format(
            event_date=data.get("event_date", "—"),
            kids_count=data.get("kids_count", "—"),
            format=data.get("format", "не выбран"),
            time=data.get("time", "—")
        )
        
        # Устанавливаем состояние
        sm = get_state_machine(user_id, platform)
        sm.set_state(BirthdayState.EXISTING_BOOKING)
        
        return FlowResponse(
            text=text,
            buttons=MESSAGES["existing_booking_buttons"],
            update_state=BirthdayState.EXISTING_BOOKING
        )
    
    async def handle_message(
        self,
        user_id: str,
        message: str,
        platform: str = "telegram",
        username: str = None,
        first_name: str = None,
        park_id: str = "nn"
    ) -> Optional[FlowResponse]:
        """
        Обработать текстовое сообщение в контексте birthday flow.
        
        Returns:
            FlowResponse если сообщение обработано, None если нужно передать AI
        """
        sm = get_state_machine(user_id, platform)
        state = sm.get_current_state()
        
        logger.info(f"Birthday message: user={user_id}, state={state.value}, msg='{message[:50]}...'")
        
        # Если flow не активен — проверяем триггеры
        if state in (BirthdayState.IDLE, BirthdayState.COMPLETE):
            if contains_birthday_trigger(message):
                return await self.handle_trigger(
                    user_id, platform, username, first_name, park_id
                )
            return None  # Не наш flow
        
        # Обрабатываем в зависимости от состояния
        handler_map = {
            BirthdayState.ASK_DATE: self._handle_date_input,
            BirthdayState.ASK_KIDS: self._handle_kids_input,
            BirthdayState.ASK_PHONE: self._handle_phone_input,
            BirthdayState.ASK_FORMAT: self._handle_format_input,
            BirthdayState.ASK_TIME: self._handle_time_input,
        }
        
        handler = handler_map.get(state)
        if handler:
            return await handler(user_id, message, platform, park_id)
        
        return None
    
    async def handle_callback(
        self,
        user_id: str,
        callback_data: str,
        platform: str = "telegram",
        park_id: str = "nn"
    ) -> Optional[FlowResponse]:
        """
        Обработать нажатие кнопки.
        
        Returns:
            FlowResponse или None
        """
        logger.info(f"Birthday callback: user={user_id}, data={callback_data}")
        
        # Существующее бронирование — кнопки действий
        if callback_data == "birthday_edit":
            return await self._handle_edit_request(user_id, platform, park_id)
        elif callback_data == "birthday_cancel":
            return await self._handle_cancel_request(user_id, platform, park_id)
        elif callback_data == "birthday_contact":
            return await self._handle_contact_request(user_id, platform, park_id)
        
        # Подтверждение телефона
        elif callback_data == "phone_confirm_yes":
            return await self._handle_phone_confirmed(user_id, platform, park_id)
        elif callback_data == "phone_confirm_no":
            return await self._handle_phone_rejected(user_id, platform, park_id)
        
        # Выбор формата
        elif callback_data == "format_room":
            return await self._handle_format_selected(user_id, "комната", platform, park_id)
        elif callback_data == "format_zone":
            return await self._handle_format_selected(user_id, "ресторан", platform, park_id)
        
        # Выбор времени
        elif callback_data.startswith("time_"):
            time_map = {
                "time_1030": "10:30",
                "time_1430": "14:30",
                "time_1830": "18:30"
            }
            time = time_map.get(callback_data, "14:30")
            return await self._handle_time_selected(user_id, time, platform, park_id)
        
        # Допуслуги
        elif callback_data == "extras_skip":
            return await self._handle_complete(user_id, platform, park_id)
        elif callback_data in ("view_cakes", "view_animators", "view_decor"):
            # Показать каталог, но не закрывать flow
            return None  # Обрабатывается в основных handlers
        
        return None
    
    # ==================== ОБРАБОТЧИКИ ШАГОВ ====================
    
    async def _handle_date_input(
        self,
        user_id: str,
        message: str,
        platform: str,
        park_id: str
    ) -> Optional[FlowResponse]:
        """Обработать ввод даты."""
        parsed_date = parse_user_date(message)
        
        if not parsed_date:
            # AI может ответить, мы возвращаем подсказку
            return FlowResponse(
                text=MESSAGES["date_not_recognized"] + "\n\n" + MESSAGES["ask_date"]
            )
        
        # Сохраняем дату
        normalized_date = format_date_ru(parsed_date, include_year=False)
        sm = get_state_machine(user_id, platform)
        sm.update_data({"event_date": normalized_date})
        sm.set_state(BirthdayState.ASK_KIDS)
        
        return FlowResponse(
            text=MESSAGES["date_confirmed"].format(date=normalized_date),
            update_state=BirthdayState.ASK_KIDS
        )
    
    async def _handle_kids_input(
        self,
        user_id: str,
        message: str,
        platform: str,
        park_id: str
    ) -> Optional[FlowResponse]:
        """Обработать ввод количества детей."""
        kids_count = parse_kids_count(message)
        
        if not kids_count:
            return FlowResponse(
                text=MESSAGES["kids_not_recognized"] + "\n\n" + MESSAGES["ask_kids"]
            )
        
        # Сохраняем
        sm = get_state_machine(user_id, platform)
        sm.update_data({"kids_count": kids_count})
        
        # Проверяем телефон в AmoCRM
        checker = BookingChecker(user_id, platform, park_id)
        phone = await checker.get_phone_from_crm()
        
        if phone:
            # Нашли телефон — предлагаем подтвердить
            sm.update_data({"pending_phone": phone})
            sm.set_state(BirthdayState.CONFIRM_PHONE)
            
            return FlowResponse(
                text=MESSAGES["kids_confirmed"].format(kids_count=kids_count) + "\n\n" +
                     MESSAGES["confirm_phone"].format(phone=phone),
                buttons=MESSAGES["confirm_phone_buttons"],
                update_state=BirthdayState.CONFIRM_PHONE
            )
        else:
            # Телефон не найден — просим ввести
            sm.set_state(BirthdayState.ASK_PHONE)
            
            return FlowResponse(
                text=MESSAGES["kids_confirmed"].format(kids_count=kids_count) + "\n\n" +
                     MESSAGES["ask_phone"],
                update_state=BirthdayState.ASK_PHONE
            )
    
    async def _handle_phone_input(
        self,
        user_id: str,
        message: str,
        platform: str,
        park_id: str
    ) -> Optional[FlowResponse]:
        """Обработать ввод телефона."""
        phone = extract_phone_from_message(message)
        
        if not phone:
            return FlowResponse(
                text=MESSAGES["phone_not_recognized"] + "\n\n" + MESSAGES["ask_phone"]
            )
        
        # Сохраняем телефон
        sm = get_state_machine(user_id, platform)
        sm.update_data({"phone": phone})
        
        # СРАЗУ СОЗДАЁМ ЗАЯВКУ В AmoCRM
        await self._send_to_crm(user_id, platform, park_id)
        
        # Переходим к выбору формата
        return await self._show_format_choice(user_id, platform, park_id)
    
    async def _handle_phone_confirmed(
        self,
        user_id: str,
        platform: str,
        park_id: str
    ) -> FlowResponse:
        """Телефон подтверждён — сохраняем и продолжаем."""
        sm = get_state_machine(user_id, platform)
        data = sm.get_collected_data()
        
        # Переносим pending_phone в phone
        pending_phone = data.get("pending_phone")
        if pending_phone:
            sm.update_data({"phone": pending_phone})
        
        # СОЗДАЁМ ЗАЯВКУ В AmoCRM
        await self._send_to_crm(user_id, platform, park_id)
        
        # Переходим к выбору формата
        return await self._show_format_choice(user_id, platform, park_id)
    
    async def _handle_phone_rejected(
        self,
        user_id: str,
        platform: str,
        park_id: str
    ) -> FlowResponse:
        """Пользователь хочет ввести другой телефон."""
        sm = get_state_machine(user_id, platform)
        sm.set_state(BirthdayState.ASK_PHONE)
        
        return FlowResponse(
            text=MESSAGES["ask_phone"],
            update_state=BirthdayState.ASK_PHONE
        )
    
    async def _handle_format_input(
        self,
        user_id: str,
        message: str,
        platform: str,
        park_id: str
    ) -> Optional[FlowResponse]:
        """Обработать текстовый выбор формата."""
        message_lower = message.lower()
        
        if any(w in message_lower for w in ["комнат", "room"]):
            return await self._handle_format_selected(user_id, "комната", platform, park_id)
        elif any(w in message_lower for w in ["рестора", "зона", "столик"]):
            return await self._handle_format_selected(user_id, "ресторан", platform, park_id)
        
        # Не распознано — показываем кнопки снова
        return await self._show_format_choice(user_id, platform, park_id)
    
    async def _handle_format_selected(
        self,
        user_id: str,
        format_value: str,
        platform: str,
        park_id: str
    ) -> FlowResponse:
        """Формат выбран."""
        sm = get_state_machine(user_id, platform)
        sm.update_data({"format": format_value})
        
        # Обновляем заявку в AmoCRM
        await self._update_crm(user_id, platform, park_id, {"format": format_value})
        
        if format_value == "комната":
            # Показываем слоты времени
            sm.set_state(BirthdayState.ASK_TIME)
            data = sm.get_collected_data()
            
            return FlowResponse(
                text=MESSAGES["format_confirmed_room"] + "\n\n" +
                     MESSAGES["ask_time"].format(date=data.get("event_date", "")),
                buttons=MESSAGES["time_buttons"],
                update_state=BirthdayState.ASK_TIME
            )
        else:
            # Ресторан — пропускаем время, сразу к допуслугам
            sm.set_state(BirthdayState.ASK_EXTRAS)
            
            return FlowResponse(
                text=MESSAGES["format_confirmed_zone"] + "\n\n" + MESSAGES["ask_extras"],
                buttons=MESSAGES["extras_buttons"],
                update_state=BirthdayState.ASK_EXTRAS
            )
    
    async def _handle_time_input(
        self,
        user_id: str,
        message: str,
        platform: str,
        park_id: str
    ) -> Optional[FlowResponse]:
        """Обработать текстовый ввод времени."""
        time_match = re.search(r'(\d{1,2})[:\.]?(\d{2})?', message)
        
        if time_match:
            hour = int(time_match.group(1))
            minute = time_match.group(2) or "00"
            time_str = f"{hour}:{minute}"
            return await self._handle_time_selected(user_id, time_str, platform, park_id)
        
        # Не распознано — показываем кнопки
        sm = get_state_machine(user_id, platform)
        data = sm.get_collected_data()
        
        return FlowResponse(
            text=MESSAGES["ask_time"].format(date=data.get("event_date", "")),
            buttons=MESSAGES["time_buttons"]
        )
    
    async def _handle_time_selected(
        self,
        user_id: str,
        time: str,
        platform: str,
        park_id: str
    ) -> FlowResponse:
        """Время выбрано."""
        sm = get_state_machine(user_id, platform)
        sm.update_data({"time": time})
        sm.set_state(BirthdayState.ASK_EXTRAS)
        
        # Обновляем AmoCRM
        await self._update_crm(user_id, platform, park_id, {"time": time})
        
        return FlowResponse(
            text=MESSAGES["time_confirmed"].format(time=time) + "\n\n" + MESSAGES["ask_extras"],
            buttons=MESSAGES["extras_buttons"],
            update_state=BirthdayState.ASK_EXTRAS
        )
    
    async def _handle_complete(
        self,
        user_id: str,
        platform: str,
        park_id: str
    ) -> FlowResponse:
        """Завершить flow."""
        sm = get_state_machine(user_id, platform)
        data = sm.get_collected_data()
        sm.set_state(BirthdayState.COMPLETE)
        
        text = MESSAGES["complete"].format(
            event_date=data.get("event_date", "—"),
            kids_count=data.get("kids_count", "—"),
            format=data.get("format", "—"),
            time=data.get("time", "—"),
            phone=data.get("phone", "—")
        )
        
        return FlowResponse(
            text=text,
            buttons=MESSAGES["complete_buttons"],
            update_state=BirthdayState.COMPLETE
        )
    
    # ==================== КНОПКИ СУЩЕСТВУЮЩЕГО БРОНИРОВАНИЯ ====================
    
    async def _handle_edit_request(
        self,
        user_id: str,
        platform: str,
        park_id: str
    ) -> FlowResponse:
        """Пользователь хочет изменить бронирование."""
        await self._notify_managers(
            user_id, platform, park_id, 
            "edit", 
            "Клиент хочет изменить бронирование"
        )
        
        return FlowResponse(text=MESSAGES["edit_request_sent"])
    
    async def _handle_cancel_request(
        self,
        user_id: str,
        platform: str,
        park_id: str
    ) -> FlowResponse:
        """Пользователь хочет отменить бронирование."""
        await self._notify_managers(
            user_id, platform, park_id,
            "cancel",
            "Клиент хочет отменить бронирование"
        )
        
        return FlowResponse(text=MESSAGES["cancel_request_sent"])
    
    async def _handle_contact_request(
        self,
        user_id: str,
        platform: str,
        park_id: str
    ) -> FlowResponse:
        """Пользователь хочет связаться с менеджером."""
        await self._notify_managers(
            user_id, platform, park_id,
            "contact",
            "Клиент просит связаться"
        )
        
        return FlowResponse(text=MESSAGES["contact_request_sent"])
    
    # ==================== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ====================
    
    async def _show_format_choice(
        self,
        user_id: str,
        platform: str,
        park_id: str
    ) -> FlowResponse:
        """Показать выбор формата с ценами."""
        sm = get_state_machine(user_id, platform)
        data = sm.get_collected_data()
        sm.set_state(BirthdayState.ASK_FORMAT)
        
        event_date = parse_user_date(data.get("event_date", ""))
        kids_count = data.get("kids_count", 0)
        
        if event_date and kids_count:
            room_calc = calculate_birthday_price(event_date, kids_count, "room", park_id)
            zone_calc = calculate_birthday_price(event_date, kids_count, "zone", park_id)
            
            text = MESSAGES["ask_format"].format(
                kids_count=kids_count,
                room_price=room_calc.get("description", ""),
                zone_price=zone_calc.get("description", "")
            )
        else:
            text = MESSAGES["phone_confirmed"] + "\n\nКакой формат праздника предпочитаете?"
        
        return FlowResponse(
            text=text,
            buttons=MESSAGES["format_buttons"],
            update_state=BirthdayState.ASK_FORMAT
        )
    
    async def _send_to_crm(
        self,
        user_id: str,
        platform: str,
        park_id: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """Отправить заявку в AmoCRM."""
        sm = get_state_machine(user_id, platform)
        data = sm.get_collected_data()
        
        try:
            lead_data = {
                "event_date": data.get("event_date"),
                "kids_count": data.get("kids_count"),
                "phone": data.get("phone"),
                "customer_name": data.get("customer_name"),
                "source": platform,
            }
            
            telegram_id = int(user_id) if platform == "telegram" else None
            vk_id = int(user_id) if platform == "vk" else None
            
            deal_id, contact_id = await send_lead_to_amocrm(
                lead_data,
                telegram_id=telegram_id,
                vk_id=vk_id,
                username=data.get("username")
            )
            
            if deal_id:
                sm.update_data({
                    "amocrm_deal_id": str(deal_id),
                    "amocrm_contact_id": str(contact_id) if contact_id else None
                })
                logger.info(f"Created AmoCRM deal {deal_id} for user {user_id}")
                
                # Уведомляем менеджеров
                msg = format_lead_message(
                    platform=platform,
                    user_id=user_id,
                    lead_data=lead_data
                )
                await send_to_birthday_channel(msg)
            
            return deal_id, contact_id
            
        except Exception as e:
            logger.error(f"Failed to send to CRM: {e}")
            return None, None
    
    async def _update_crm(
        self,
        user_id: str,
        platform: str,
        park_id: str,
        update_data: Dict[str, Any]
    ) -> bool:
        """Обновить заявку в AmoCRM."""
        sm = get_state_machine(user_id, platform)
        data = sm.get_collected_data()
        deal_id = data.get("amocrm_deal_id")
        
        if not deal_id:
            logger.warning(f"No deal_id to update for user {user_id}")
            return False
        
        try:
            await amocrm_client.update_deal_fields(int(deal_id), update_data)
            logger.info(f"Updated AmoCRM deal {deal_id}: {list(update_data.keys())}")
            return True
        except Exception as e:
            logger.error(f"Failed to update CRM: {e}")
            return False
    
    async def _notify_managers(
        self,
        user_id: str,
        platform: str,
        park_id: str,
        action_type: str,
        message: str
    ):
        """Отправить уведомление менеджерам + создать задачу в AmoCRM."""
        sm = get_state_machine(user_id, platform)
        data = sm.get_collected_data()
        deal_id = data.get("amocrm_deal_id")
        
        # Формируем сообщение
        booking_info = (
            f"📅 Дата: {data.get('event_date', '—')}\n"
            f"👶 Детей: {data.get('kids_count', '—')}\n"
            f"📱 Телефон: {data.get('phone', '—')}\n"
            f"🔗 Платформа: {platform}"
        )
        
        notification = f"📝 {message}\n\n{booking_info}"
        
        # Отправляем в Telegram канал
        await send_to_birthday_channel(notification)
        
        # Создаём задачу в AmoCRM
        if deal_id:
            try:
                await amocrm_client.create_task(
                    int(deal_id),
                    message
                )
            except Exception as e:
                logger.error(f"Failed to create task in AmoCRM: {e}")


# Singleton instance
birthday_flow_handler = BirthdayFlowHandler()
