"""
BookingChecker — проверка существующих бронирований в AmoCRM.

Главный принцип: AmoCRM — единый источник правды.
Если в CRM нет бронирования — очищаем локальную БД.
"""

import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from datetime import datetime, date

from core.amocrm import amocrm_client
from db import SessionLocal, Lead

logger = logging.getLogger(__name__)


@dataclass
class BookingStatus:
    """Результат проверки бронирования."""
    has_booking: bool
    booking_data: Dict[str, Any] = None
    deal_id: str = None
    contact_id: str = None
    is_past_date: bool = False
    
    def __post_init__(self):
        if self.booking_data is None:
            self.booking_data = {}


class BookingChecker:
    """
    Проверяет существующие бронирования в AmoCRM.
    
    Логика:
    1. Ищем контакт по telegram_id / vk_id
    2. Получаем сделки контакта
    3. Фильтруем: только активные, с датой в будущем
    4. Если нет — очищаем локальные данные
    """
    
    def __init__(self, user_id: str, platform: str = "telegram", park_id: str = "nn"):
        self.user_id = str(user_id)
        self.platform = platform
        self.park_id = park_id
    
    async def check_existing_booking(self) -> BookingStatus:
        """
        Проверяет AmoCRM на наличие активного бронирования.
        
        Returns:
            BookingStatus с данными бронирования или пустой статус
        """
        try:
            # 1. Ищем контакт в AmoCRM
            contact = await self._find_contact()
            if not contact:
                logger.info(f"No contact found in AmoCRM for user {self.user_id}")
                await self._clean_local_data()
                return BookingStatus(has_booking=False)
            
            contact_id = contact.get("id")
            
            # 2. Получаем сделки контакта
            deals = await amocrm_client.get_deals_for_contact(contact_id)
            if not deals:
                logger.info(f"No deals found for contact {contact_id}")
                await self._clean_local_data()
                return BookingStatus(has_booking=False, contact_id=str(contact_id))
            
            # 3. Ищем активную сделку ДР с датой в будущем
            active_deal = self._find_active_birthday_deal(deals)
            if not active_deal:
                logger.info(f"No active birthday deals for contact {contact_id}")
                await self._clean_local_data()
                return BookingStatus(has_booking=False, contact_id=str(contact_id))
            
            # 4. Есть активное бронирование
            booking_data = self._extract_booking_data(active_deal)
            logger.info(f"Found active booking for user {self.user_id}: {booking_data}")
            
            return BookingStatus(
                has_booking=True,
                booking_data=booking_data,
                deal_id=str(active_deal.get("id")),
                contact_id=str(contact_id)
            )
            
        except Exception as e:
            logger.error(f"Error checking booking in AmoCRM: {e}")
            # При ошибке API не очищаем локальные данные
            return await self._check_local_booking()
    
    async def _find_contact(self) -> Optional[Dict]:
        """Найти контакт в AmoCRM по ID платформы."""
        if self.platform == "telegram":
            return await amocrm_client.find_contact_by_telegram_id(int(self.user_id))
        elif self.platform == "vk":
            return await amocrm_client.find_contact_by_vk_id(int(self.user_id))
        else:
            # Web — ищем по сессии или телефону (fallback)
            return None
    
    def _find_active_birthday_deal(self, deals: List[Dict]) -> Optional[Dict]:
        """
        Найти активную сделку ДР.
        
        Критерии:
        - Дата события в будущем
        - Статус не "закрыто" / "отклонено"
        """
        today = date.today()
        
        for deal in deals:
            # Проверяем дату
            event_date_str = deal.get("event_date")
            if not event_date_str:
                continue
            
            # Парсим дату
            event_date = self._parse_date(event_date_str)
            if not event_date:
                continue
            
            # Дата в прошлом?
            if event_date < today:
                logger.debug(f"Deal {deal.get('id')} has past date: {event_date_str}")
                continue
            
            # Проверяем статус (не закрыто)
            status = deal.get("status_id")
            # TODO: добавить проверку конкретных статусов "закрыто"
            
            return deal
        
        return None
    
    def _parse_date(self, date_str: str) -> Optional[date]:
        """Распарсить дату из строки."""
        from core.utils import parse_user_date
        
        try:
            result = parse_user_date(date_str)
            if result:
                return result.date() if hasattr(result, 'date') else result
        except Exception:
            pass
        return None
    
    def _extract_booking_data(self, deal: Dict) -> Dict[str, Any]:
        """Извлечь данные бронирования из сделки."""
        return {
            "event_date": deal.get("event_date"),
            "kids_count": deal.get("kids_count"),
            "format": deal.get("format"),
            "time": deal.get("time"),
            "room": deal.get("room"),
            "phone": deal.get("phone"),
            "customer_name": deal.get("customer_name"),
            "extras": deal.get("extras"),
        }
    
    async def _clean_local_data(self) -> int:
        """
        Очистить локальные данные если нет бронирования в CRM.
        
        Returns:
            Количество очищенных записей
        """
        db = SessionLocal()
        try:
            # Определяем поле фильтрации
            if self.platform == "telegram":
                filter_condition = Lead.telegram_id == self.user_id
            elif self.platform == "vk":
                filter_condition = Lead.vk_id == self.user_id
            else:
                return 0
            
            # Находим лиды без amocrm_deal_id
            orphan_leads = db.query(Lead).filter(
                filter_condition,
                Lead.park_id == self.park_id,
                Lead.amocrm_deal_id == None,
                Lead.status.in_(["new", "contacted", "booked"])
            ).all()
            
            count = len(orphan_leads)
            for lead in orphan_leads:
                lead.status = "cancelled"
                lead.birthday_state = "idle"
                logger.info(f"Cleaned orphan Lead #{lead.id}")
            
            if count > 0:
                db.commit()
            
            # Также сбрасываем состояние у лидов с прошедшей датой
            await self._clean_past_bookings(db, filter_condition)
            
            return count
        except Exception as e:
            logger.error(f"Error cleaning local data: {e}")
            db.rollback()
            return 0
        finally:
            db.close()
    
    async def _clean_past_bookings(self, db, filter_condition) -> int:
        """Очистить бронирования с прошедшей датой."""
        today = date.today()
        
        # Находим лиды с прошедшей датой
        leads = db.query(Lead).filter(
            filter_condition,
            Lead.park_id == self.park_id,
            Lead.status.in_(["new", "contacted", "booked"])
        ).all()
        
        count = 0
        for lead in leads:
            if lead.event_date:
                event_date = self._parse_date(lead.event_date)
                if event_date and event_date < today:
                    lead.status = "completed"
                    lead.birthday_state = "idle"
                    count += 1
                    logger.info(f"Marked Lead #{lead.id} as completed (past date)")
        
        if count > 0:
            db.commit()
        
        return count
    
    async def _check_local_booking(self) -> BookingStatus:
        """Fallback: проверить локальную БД если AmoCRM недоступен."""
        db = SessionLocal()
        try:
            if self.platform == "telegram":
                filter_condition = Lead.telegram_id == self.user_id
            elif self.platform == "vk":
                filter_condition = Lead.vk_id == self.user_id
            else:
                return BookingStatus(has_booking=False)
            
            lead = db.query(Lead).filter(
                filter_condition,
                Lead.park_id == self.park_id,
                Lead.amocrm_deal_id != None,
                Lead.status.in_(["new", "contacted", "booked"])
            ).order_by(Lead.id.desc()).first()
            
            if lead and lead.event_date:
                return BookingStatus(
                    has_booking=True,
                    booking_data={
                        "event_date": lead.event_date,
                        "kids_count": lead.kids_count,
                        "format": lead.format,
                        "time": lead.time,
                        "phone": lead.phone,
                        "customer_name": lead.customer_name,
                    },
                    deal_id=lead.amocrm_deal_id,
                    contact_id=lead.amocrm_contact_id
                )
            
            return BookingStatus(has_booking=False)
        finally:
            db.close()
    
    async def get_phone_from_crm(self) -> Optional[str]:
        """Получить телефон клиента из AmoCRM."""
        try:
            contact = await self._find_contact()
            if contact:
                contact_info = amocrm_client.get_contact_info(contact)
                return contact_info.get("phone")
        except Exception as e:
            logger.error(f"Error getting phone from CRM: {e}")
        return None
    
    async def get_customer_name_from_crm(self) -> Optional[str]:
        """Получить имя клиента из AmoCRM."""
        try:
            contact = await self._find_contact()
            if contact:
                contact_info = amocrm_client.get_contact_info(contact)
                return contact_info.get("name")
        except Exception as e:
            logger.error(f"Error getting name from CRM: {e}")
        return None
