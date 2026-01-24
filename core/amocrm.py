"""
AmoCRM Integration Module.
Handles OAuth2 authentication and API calls to AmoCRM.
"""

import os
import json
import logging
import httpx
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from pathlib import Path

logger = logging.getLogger(__name__)


class AmoCRMClient:
    """Client for AmoCRM API v4."""
    
    def __init__(self):
        self.domain = os.getenv("AMOCRM_DOMAIN", "")
        self.client_id = os.getenv("AMOCRM_CLIENT_ID", "")
        self.client_secret = os.getenv("AMOCRM_CLIENT_SECRET", "")
        self.redirect_uri = os.getenv("AMOCRM_REDIRECT_URI", "")
        self.pipeline_id = os.getenv("AMOCRM_PIPELINE_ID", "")
        
        self.base_url = f"https://{self.domain}"
        self.token_file = Path(__file__).parent.parent / "data" / "amocrm_tokens.json"
        
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        
        self._load_tokens()
    
    def _load_tokens(self):
        """Load tokens from file."""
        if self.token_file.exists():
            try:
                data = json.loads(self.token_file.read_text())
                self._access_token = data.get("access_token")
                self._refresh_token = data.get("refresh_token")
                expires_at = data.get("expires_at")
                if expires_at:
                    self._token_expires_at = datetime.fromisoformat(expires_at)
                logger.info("AmoCRM tokens loaded from file")
            except Exception as e:
                logger.error(f"Failed to load AmoCRM tokens: {e}")
    
    def _save_tokens(self):
        """Save tokens to file."""
        try:
            self.token_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "access_token": self._access_token,
                "refresh_token": self._refresh_token,
                "expires_at": self._token_expires_at.isoformat() if self._token_expires_at else None
            }
            self.token_file.write_text(json.dumps(data, indent=2))
            logger.info("AmoCRM tokens saved to file")
        except Exception as e:
            logger.error(f"Failed to save AmoCRM tokens: {e}")
    
    async def exchange_code_for_tokens(self, code: str) -> bool:
        """Exchange authorization code for access tokens."""
        url = f"{self.base_url}/oauth2/access_token"
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=data)
            
            if response.status_code == 200:
                tokens = response.json()
                self._access_token = tokens["access_token"]
                self._refresh_token = tokens["refresh_token"]
                self._token_expires_at = datetime.now() + timedelta(seconds=tokens["expires_in"])
                self._save_tokens()
                logger.info("AmoCRM tokens obtained successfully")
                return True
            else:
                logger.error(f"Failed to exchange code: {response.status_code} - {response.text}")
                return False
    
    async def refresh_tokens(self) -> bool:
        """Refresh access token using refresh token."""
        if not self._refresh_token:
            logger.error("No refresh token available")
            return False
        
        url = f"{self.base_url}/oauth2/access_token"
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "redirect_uri": self.redirect_uri
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=data)
            
            if response.status_code == 200:
                tokens = response.json()
                self._access_token = tokens["access_token"]
                self._refresh_token = tokens["refresh_token"]
                self._token_expires_at = datetime.now() + timedelta(seconds=tokens["expires_in"])
                self._save_tokens()
                logger.info("AmoCRM tokens refreshed successfully")
                return True
            else:
                logger.error(f"Failed to refresh tokens: {response.status_code} - {response.text}")
                return False
    
    async def _ensure_token(self) -> bool:
        """Ensure we have a valid access token."""
        if not self._access_token:
            logger.error("No access token available. Need to authorize first.")
            return False
        
        if self._token_expires_at and datetime.now() >= self._token_expires_at - timedelta(minutes=5):
            return await self.refresh_tokens()
        
        return True
    
    async def _request(self, method: str, endpoint: str, **kwargs) -> Optional[Dict]:
        """Make an authenticated API request."""
        if not await self._ensure_token():
            return None
        
        url = f"{self.base_url}/api/v4{endpoint}"
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.request(method, url, headers=headers, **kwargs)
            
            if response.status_code in (200, 201):
                return response.json()
            elif response.status_code == 204:
                return {}
            else:
                logger.error(f"AmoCRM API error: {response.status_code} - {response.text}")
                return None
    
    async def find_contact_by_phone(self, phone: str) -> Optional[Dict]:
        """Find contact by phone number."""
        if not phone or phone == "null":
            logger.info("Empty phone provided, skipping contact search")
            return None
        
        # Normalize phone - get last 10 digits
        phone_clean = ''.join(filter(str.isdigit, phone))
        if len(phone_clean) < 10:
            logger.info(f"Phone too short: {phone_clean}, skipping contact search")
            return None
        
        if len(phone_clean) > 10:
            phone_clean = phone_clean[-10:]  # Last 10 digits (without country code)
        
        logger.info(f"Searching for contact with phone: {phone_clean}")
        result = await self._request("GET", f"/contacts?query={phone_clean}")
        if result and result.get("_embedded", {}).get("contacts"):
            logger.info(f"Found contact: {result['_embedded']['contacts'][0].get('id')}")
            return result["_embedded"]["contacts"][0]
        logger.info("Contact not found")
        return None
    
    async def create_contact(self, data: Dict[str, Any]) -> Optional[int]:
        """
        Create a new contact in AmoCRM.
        
        Args:
            data: dict with keys: name, phone, telegram_id, vk_id, username
        
        Returns:
            Contact ID or None
        """
        # Custom field IDs
        TELEGRAM_ID_FIELD = 1130365
        TELEGRAM_USERNAME_FIELD = 1130363
        VK_ID_FIELD = 1131123
        
        contact_data = [{
            "name": data.get("name", "Клиент"),
            "custom_fields_values": []
        }]
        
        # Add phone
        if data.get("phone"):
            contact_data[0]["custom_fields_values"].append({
                "field_code": "PHONE",
                "values": [{"value": data["phone"], "enum_code": "WORK"}]
            })
        
        # Add Telegram ID
        if data.get("telegram_id"):
            contact_data[0]["custom_fields_values"].append({
                "field_id": TELEGRAM_ID_FIELD,
                "values": [{"value": str(data["telegram_id"])}]
            })
        
        # Add Telegram Username
        if data.get("username"):
            contact_data[0]["custom_fields_values"].append({
                "field_id": TELEGRAM_USERNAME_FIELD,
                "values": [{"value": data["username"]}]
            })
        
        # Add VK ID
        if data.get("vk_id"):
            contact_data[0]["custom_fields_values"].append({
                "field_id": VK_ID_FIELD,
                "values": [{"value": str(data["vk_id"])}]
            })
        
        result = await self._request("POST", "/contacts", json=contact_data)
        if result and result.get("_embedded", {}).get("contacts"):
            contact_id = result["_embedded"]["contacts"][0]["id"]
            logger.info(f"Created AmoCRM contact {contact_id} with telegram_id={data.get('telegram_id')}, vk_id={data.get('vk_id')}")
            return contact_id
        return None
    
    async def update_contact_name(self, contact_id: int, new_name: str) -> bool:
        """
        Update contact name in AmoCRM.
        
        Args:
            contact_id: AmoCRM contact ID
            new_name: New name for the contact
        
        Returns:
            True if successful
        """
        if not new_name:
            return False
            
        update_data = {
            "id": contact_id,
            "name": new_name
        }
        
        result = await self._request("PATCH", f"/contacts/{contact_id}", json=update_data)
        if result:
            logger.info(f"Updated contact {contact_id} name to: {new_name}")
            return True
        return False
    
    async def find_contact_by_telegram_id(self, telegram_id: int) -> Optional[Dict]:
        """
        Search for contact by Telegram ID custom field.
        
        Returns:
            Contact dict or None
        """
        TELEGRAM_ID_FIELD = 1130365
        
        # Search by custom field query
        params = f"query={telegram_id}"
        result = await self._request("GET", f"/contacts?{params}")
        
        if result and result.get("_embedded", {}).get("contacts"):
            contacts = result["_embedded"]["contacts"]
            # Check each contact for matching Telegram ID
            for contact in contacts:
                custom_fields = contact.get("custom_fields_values", []) or []
                for field in custom_fields:
                    if field.get("field_id") == TELEGRAM_ID_FIELD:
                        values = field.get("values", [])
                        if values and str(values[0].get("value")) == str(telegram_id):
                            logger.info(f"Found contact {contact['id']} by telegram_id {telegram_id}")
                            return contact
        return None
    
    async def find_contact_by_vk_id(self, vk_id: int) -> Optional[Dict]:
        """
        Search for contact by VK ID custom field.
        
        Returns:
            Contact dict or None
        """
        VK_ID_FIELD = 1131123
        
        # Search by custom field query
        params = f"query={vk_id}"
        result = await self._request("GET", f"/contacts?{params}")
        
        if result and result.get("_embedded", {}).get("contacts"):
            contacts = result["_embedded"]["contacts"]
            # Check each contact for matching VK ID
            for contact in contacts:
                custom_fields = contact.get("custom_fields_values", []) or []
                for field in custom_fields:
                    if field.get("field_id") == VK_ID_FIELD:
                        values = field.get("values", [])
                        if values and str(values[0].get("value")) == str(vk_id):
                            logger.info(f"Found contact {contact['id']} by vk_id {vk_id}")
                            return contact
        return None

    def get_contact_info(self, contact: Dict) -> Dict[str, str]:
        """
        Extract name and phone from a contact dict.
        
        Returns:
            Dict with 'name' and 'phone' keys
        """
        if not contact:
            return {"name": None, "phone": None}
        
        name = contact.get("name")
        phone = None
        
        # Extract phone from custom fields
        custom_fields = contact.get("custom_fields_values", []) or []
        for field in custom_fields:
            if field.get("field_code") == "PHONE":
                values = field.get("values", [])
                if values:
                    phone = values[0].get("value")
                    break
        
        logger.info(f"Extracted contact info: name={name}, phone={phone}")
        return {"name": name, "phone": phone}
    
    async def find_or_create_contact(self, data: Dict[str, Any]) -> Optional[int]:
        """
        Find existing contact by phone OR telegram_id, or create new.
        This enables contact merging across TG and VK.
        
        Args:
            data: dict with phone, telegram_id, vk_id, name, username
            
        Returns:
            Contact ID
        """
        phone = data.get("phone")
        telegram_id = data.get("telegram_id")
        vk_id = data.get("vk_id")
        
        # 1. Try to find by phone (primary merge key)
        if phone:
            existing = await self.find_contact_by_phone(phone)
            if existing:
                contact_id = existing.get("id")
                # Update with new social IDs and username if provided (merge contacts)
                await self.update_contact_social_ids(
                    contact_id, 
                    telegram_id=telegram_id, 
                    vk_id=vk_id,
                    telegram_username=data.get("username")
                )
                logger.info(f"Found existing contact {contact_id} by phone {phone}, merged social IDs")
                return contact_id
        
        # 2. Try to find by Telegram ID
        if telegram_id:
            existing = await self.find_contact_by_telegram_id(telegram_id)
            if existing:
                contact_id = existing.get("id")
                logger.info(f"Found existing contact {contact_id} by telegram_id {telegram_id}")
                return contact_id
        
        # 3. Create new contact
        contact_id = await self.create_contact(data)
        return contact_id
    
    async def update_contact_social_ids(self, contact_id: int, telegram_id: int = None, vk_id: int = None, telegram_username: str = None) -> bool:
        """
        Add Telegram ID, VK ID or Telegram username to existing contact.
        Used for merging contacts when phone matches.
        """
        TELEGRAM_ID_FIELD = 1130365
        TELEGRAM_USERNAME_FIELD = 1130363
        VK_ID_FIELD = 1131123
        
        custom_fields = []
        
        if telegram_id:
            custom_fields.append({
                "field_id": TELEGRAM_ID_FIELD,
                "values": [{"value": str(telegram_id)}]
            })
        
        if telegram_username:
            custom_fields.append({
                "field_id": TELEGRAM_USERNAME_FIELD,
                "values": [{"value": telegram_username}]
            })
        
        if vk_id:
            custom_fields.append({
                "field_id": VK_ID_FIELD,
                "values": [{"value": str(vk_id)}]
            })
        
        if not custom_fields:
            return True
        
        update_data = {
            "id": contact_id,
            "custom_fields_values": custom_fields
        }
        
        result = await self._request("PATCH", f"/contacts/{contact_id}", json=update_data)
        if result:
            logger.info(f"Updated contact {contact_id} social IDs: tg={telegram_id}, username={telegram_username}, vk={vk_id}")
            return True
        return False
    
    async def get_deals_for_contact(self, contact_id: int) -> List[Dict]:
        """
        Get all deals associated with a contact.
        
        Returns:
            List of deal dicts with full details
        """
        result = await self._request("GET", f"/contacts/{contact_id}/links")
        
        if not result or not result.get("_embedded"):
            return []
        
        links = result["_embedded"].get("links", [])
        deal_ids = [link["to_entity_id"] for link in links if link.get("to_entity_type") == "leads"]
        
        if not deal_ids:
            return []
        
        # Fetch deal details
        deals = []
        for deal_id in deal_ids[:5]:  # Limit to 5 recent deals
            deal = await self.get_deal_details(deal_id)
            if deal:
                deals.append(deal)
        
        return deals
    
    async def get_deal_details(self, deal_id: int) -> Optional[Dict]:
        """
        Get full deal data from AmoCRM.
        
        Returns:
            Dict with deal info including custom fields parsed
        """
        result = await self._request("GET", f"/leads/{deal_id}")
        
        if not result:
            return None
        
        # Parse custom fields into readable format
        custom_fields = result.get("custom_fields_values", []) or []
        parsed = {
            "deal_id": deal_id,
            "name": result.get("name"),
            "price": result.get("price"),
            "status_id": result.get("status_id"),
            "created_at": result.get("created_at"),
        }
        
        # Map field IDs to names
        FIELD_MAP = {
            1050741: "event_date",
            1072647: "event_time", 
            1050745: "child_name",
            1072649: "kids_count",
            1050747: "adults_count",
            1072635: "room",
            1051037: "child_age",
            1051041: "extras"
        }
        
        for field in custom_fields:
            field_id = field.get("field_id")
            field_name = FIELD_MAP.get(field_id)
            if field_name:
                values = field.get("values", [])
                if values:
                    parsed[field_name] = values[0].get("value")
        
        return parsed
    
    async def create_deal(self, contact_id: int, lead_data: Dict[str, Any]) -> Optional[int]:
        """
        Create a new deal (sdelka) in AmoCRM.
        
        Args:
            contact_id: AmoCRM contact ID
            lead_data: dict with lead info from bot
        
        Returns:
            Deal ID or None
        """
        # Определяем платформу для названия сделки
        source = (lead_data.get('source') or '').lower()
        if source == 'telegram':
            platform_label = 'ИИ бот (TG)'
        elif source == 'vk':
            platform_label = 'ИИ бот (VK)'
        elif source in ('web', 'website', 'site'):
            platform_label = 'ИИ бот (Web)'
        else:
            platform_label = 'ИИ бот'
        
        deal_name = f"ДР - {platform_label}"
        
        deal_data = [{
            "name": deal_name,
            "pipeline_id": int(self.pipeline_id) if self.pipeline_id else None,
            "_embedded": {
                "contacts": [{"id": contact_id}]
            },
            "custom_fields_values": []
        }]
        
        # Remove pipeline_id if not set
        if not self.pipeline_id:
            del deal_data[0]["pipeline_id"]
        
        # Map fields from lead_data to AmoCRM custom fields
        # TODO: Add actual field IDs after getting them from AmoCRM
        custom_fields = self._map_lead_to_custom_fields(lead_data)
        deal_data[0]["custom_fields_values"] = custom_fields
        
        result = await self._request("POST", "/leads", json=deal_data)
        if result and result.get("_embedded", {}).get("leads"):
            deal_id = result["_embedded"]["leads"][0]["id"]
            logger.info(f"Created AmoCRM deal {deal_id}")
            return deal_id
        return None
    
    def _parse_date_to_timestamp(self, date_str: str, time_str: str = None) -> Optional[int]:
        """
        Parse Russian date string to Unix timestamp.
        Handles formats like: "3 сентября", "25.01.2026", "8 августа 2026"
        If time_str is provided (e.g. "14:00", "15:30"), it will be used instead of default 12:00
        
        Time is interpreted as Moscow timezone (UTC+3) since clients are in Russia.
        """
        if not date_str:
            return None
        
        import re
        from datetime import timezone, timedelta
        
        # Moscow timezone (UTC+3)
        MSK = timezone(timedelta(hours=3))
        
        # Parse time if provided (formats: "14:00", "15:30", "14.00", "14 30")
        hour, minute = 12, 0  # default noon
        if time_str:
            time_match = re.search(r'(\d{1,2})[\.:, ](\d{2})', str(time_str))
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2))
                # Validate hour/minute
                if hour > 23:
                    hour = 12
                if minute > 59:
                    minute = 0
                logger.info(f"Parsed time: {hour}:{minute:02d} from '{time_str}'")
        
        # Russian month names mapping
        MONTHS_RU = {
            'января': 1, 'январь': 1,
            'февраля': 2, 'февраль': 2,
            'марта': 3, 'март': 3,
            'апреля': 4, 'апрель': 4,
            'мая': 5, 'май': 5,
            'июня': 6, 'июнь': 6,
            'июля': 7, 'июль': 7,
            'августа': 8, 'август': 8,
            'сентября': 9, 'сентябрь': 9,
            'октября': 10, 'октябрь': 10,
            'ноября': 11, 'ноябрь': 11,
            'декабря': 12, 'декабрь': 12
        }
        
        try:
            # Try format "DD.MM.YYYY"
            match = re.match(r'(\d{1,2})\.(\d{1,2})\.(\d{4})', date_str)
            if match:
                day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
                # Create datetime with Moscow timezone
                dt = datetime(year, month, day, hour, minute, tzinfo=MSK)
                return int(dt.timestamp())
            
            # Try format "D месяц" or "D месяц YYYY"
            match = re.match(r'(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?', date_str.lower())
            if match:
                day = int(match.group(1))
                month_name = match.group(2)
                year = int(match.group(3)) if match.group(3) else datetime.now().year
                
                month = MONTHS_RU.get(month_name)
                if month:
                    # Create datetime with Moscow timezone
                    dt = datetime(year, month, day, hour, minute, tzinfo=MSK)
                    # If date is in the past for current year, assume next year
                    now_msk = datetime.now(MSK)
                    if dt < now_msk and not match.group(3):
                        dt = datetime(year + 1, month, day, hour, minute, tzinfo=MSK)
                    return int(dt.timestamp())
            
            logger.warning(f"Could not parse date: {date_str}")
            return None
            
        except Exception as e:
            logger.error(f"Error parsing date '{date_str}': {e}")
            return None
    
    def _map_lead_to_custom_fields(self, lead_data: Dict[str, Any]) -> list:
        """
        Map bot lead data to AmoCRM custom field format.
        Field IDs obtained from AmoCRM API.
        """
        custom_fields = []
        
        # AmoCRM field IDs for "Банкеты НН" pipeline
        FIELD_IDS = {
            "event_date": 1050741,       # План. дата банкета (date_time)
            "format": 1072635,           # Домик или ресторан (radiobutton)
            "kids_count": 1072649,       # Кол-во детей (numeric)
            "adults_count": 1072651,     # Кол-во взрослых (numeric)
            "animator": 1072637,         # Аниматор (checkbox)
            "cake": 1072641,             # Торт (checkbox)
            "balloons": 1072643,         # Шарики (checkbox)
            "photographer": 1072647,     # Фотограф (checkbox)
            "aquagrim": 1102151,         # Аквагрим (checkbox)
            "pinata": 1102153,           # Пиньята (checkbox)
            "source": 1041515,           # Источник лида (select)
            "banquet_type": 1102145,     # Тип банкета (select)
        }
        
        # Event date + time (convert to Unix timestamp)
        if lead_data.get("event_date"):
            # Передаём время слота если оно есть
            time_slot = lead_data.get("time")
            timestamp = self._parse_date_to_timestamp(lead_data["event_date"], time_slot)
            if timestamp:
                custom_fields.append({
                    "field_id": FIELD_IDS["event_date"],
                    "values": [{"value": timestamp}]
                })
                if time_slot:
                    logger.info(f"Mapped event_date with time: {lead_data['event_date']} {time_slot} -> timestamp {timestamp}")
        
        # Kids count
        if lead_data.get("kids_count"):
            custom_fields.append({
                "field_id": FIELD_IDS["kids_count"],
                "values": [{"value": int(lead_data["kids_count"])}]
            })
        
        # Adults count
        if lead_data.get("adults_count"):
            custom_fields.append({
                "field_id": FIELD_IDS["adults_count"],
                "values": [{"value": int(lead_data["adults_count"])}]
            })
        
        # Source (Источник лида) - enum values from AmoCRM
        SOURCE_ENUMS = {
            "telegram": 975585,   # Telegram
            "vk": 659703,         # VK группа
            "web": 658465,        # Сайт "Бронируй"
            "website": 658465,    # alias
            "site": 658465,       # alias
        }
        
        source = (lead_data.get("source") or "").lower()
        if source in SOURCE_ENUMS:
            custom_fields.append({
                "field_id": FIELD_IDS["source"],
                "values": [{"enum_id": SOURCE_ENUMS[source]}]
            })
        
        # Format (room/restaurant) - radiobutton field
        # Бот использует "комната"/"ресторан", AmoCRM хранит "Домик"/"Ресторан"
        format_value = (lead_data.get("format") or "").lower()
        room_value = (lead_data.get("room") or "").lower()
        
        FORMAT_ENUMS = {
            # Комната/Домик
            "тематическая комната": 652955,
            "комната": 652955,
            "домик": 652955,
            "room": 652955,
            # Ресторан
            "ресторан": 652957,
            "столик в ресторане": 652957,
            "столик": 652957,
            "restaurant": 652957,
        }
        
        format_enum_id = FORMAT_ENUMS.get(format_value) or FORMAT_ENUMS.get(room_value)
        if format_enum_id:
            custom_fields.append({
                "field_id": FIELD_IDS["format"],
                "values": [{"enum_id": format_enum_id}]
            })
            logger.info(f"Added format field: enum_id={format_enum_id}")
        
        # Extras from lead_data["extras"] list
        extras = lead_data.get("extras", [])
        if isinstance(extras, str):
            try:
                import json
                extras = json.loads(extras)
            except:
                extras = [extras] if extras else []
        
        extras_lower = [(e or "").lower() for e in extras] if extras else []
        
        # Animator
        if any("аниматор" in e for e in extras_lower):
            custom_fields.append({
                "field_id": FIELD_IDS["animator"],
                "values": [{"value": True}]
            })
        
        # Cake
        if any("торт" in e for e in extras_lower):
            custom_fields.append({
                "field_id": FIELD_IDS["cake"],
                "values": [{"value": True}]
            })
        
        # Balloons
        if any("шар" in e for e in extras_lower):
            custom_fields.append({
                "field_id": FIELD_IDS["balloons"],
                "values": [{"value": True}]
            })
        
        # Photographer
        if any("фото" in e for e in extras_lower):
            custom_fields.append({
                "field_id": FIELD_IDS["photographer"],
                "values": [{"value": True}]
            })
        
        # Aquagrim
        if any("аквагрим" in e or "грим" in e for e in extras_lower):
            custom_fields.append({
                "field_id": FIELD_IDS["aquagrim"],
                "values": [{"value": True}]
            })
        
        # Pinata
        if any("пиньята" in e or "пинь" in e for e in extras_lower):
            custom_fields.append({
                "field_id": FIELD_IDS["pinata"],
                "values": [{"value": True}]
            })
        
        return custom_fields
    
    async def update_deal(self, deal_id: int, data: Dict[str, Any]) -> bool:
        """Update an existing deal."""
        result = await self._request("PATCH", f"/leads/{deal_id}", json=data)
        return result is not None
    
    async def update_deal_fields(self, deal_id: int, lead_data: Dict[str, Any]) -> bool:
        """
        Update an existing deal with lead_data.
        Maps lead_data to custom fields and updates the deal.
        """
        logger.info(f"=== update_deal_fields called for deal {deal_id} ===")
        logger.info(f"lead_data: {lead_data}")
        
        custom_fields = self._map_lead_to_custom_fields(lead_data)
        logger.info(f"custom_fields to update: {custom_fields}")
        
        if not custom_fields:
            logger.info(f"No fields to update for deal {deal_id}")
            return True
        
        update_data = {
            "custom_fields_values": custom_fields
        }
        
        # Update deal name if customer_name changed
        if lead_data.get("customer_name"):
            # Определяем платформу для названия сделки
            source = (lead_data.get('source') or '').lower()
            if source == 'telegram':
                platform_label = 'ИИ бот (TG)'
            elif source == 'vk':
                platform_label = 'ИИ бот (VK)'
            elif source in ('web', 'website', 'site'):
                platform_label = 'ИИ бот (Web)'
            else:
                platform_label = 'ИИ бот'
            
            update_data["name"] = f"ДР - {platform_label}"
        
        # Проверяем статус сделки — обновляем ТОЛЬКО если в первых двух статусах
        # (Неразобранное или Новый лид / получена заявка)
        deal = await self.get_deal(deal_id)
        if deal:
            current_status_id = deal.get("status_id")
            pipeline_id = deal.get("pipeline_id")
            
            # Получаем разрешённые статусы (первые два в воронке)
            allowed_status_ids = await self._get_allowed_status_ids(pipeline_id)
            
            if current_status_id not in allowed_status_ids:
                logger.info(f"Deal {deal_id} is in 'Взято в работу' or later (status: {current_status_id}), skipping update")
                return False
        
        logger.info(f"Sending PATCH request for deal {deal_id}")
        result = await self._request("PATCH", f"/leads/{deal_id}", json=update_data)
        if result:
            logger.info(f"SUCCESS: Updated AmoCRM deal {deal_id} with new fields")
            return True
        logger.error(f"FAILED: Could not update deal {deal_id}")
        return False
    
    async def _get_allowed_status_ids(self, pipeline_id: int) -> set:
        """Get status IDs where bot is allowed to update (first 2 statuses: Неразобранное + Новый лид)."""
        if not pipeline_id:
            return set()
        
        result = await self._request("GET", f"/leads/pipelines/{pipeline_id}")
        if result:
            statuses = result.get("_embedded", {}).get("statuses", [])
            if statuses:
                # Сортируем статусы по порядку
                sorted_statuses = sorted(statuses, key=lambda x: x.get("sort", 999999))
                # Разрешаем первые ДВА статуса (Неразобранное + Новый лид)
                allowed = {s.get("id") for s in sorted_statuses[:2] if s.get("id")}
                logger.info(f"Allowed statuses for updates: {allowed}")
                return allowed
        return set()
    
    async def _get_work_status_ids(self, pipeline_id: int) -> set:
        """Get status IDs that mean 'taken to work' or later (bot should not update)."""
        if not pipeline_id:
            return set()
        
        result = await self._request("GET", f"/leads/pipelines/{pipeline_id}")
        if result:
            statuses = result.get("_embedded", {}).get("statuses", [])
            if statuses:
                # Сортируем статусы по порядку
                sorted_statuses = sorted(statuses, key=lambda x: x.get("sort", 999999))
                # Первый статус — "Новый лид", можно обновлять
                # Все остальные — блокируем
                if len(sorted_statuses) > 1:
                    # Возвращаем ID всех статусов кроме первого
                    return {s.get("id") for s in sorted_statuses[1:] if s.get("id")}
        return set()
    
    async def _get_first_status_id(self, pipeline_id: int) -> Optional[int]:
        """Get the first status ID of a pipeline (new lead status)."""
        if not pipeline_id:
            return None
        
        result = await self._request("GET", f"/leads/pipelines/{pipeline_id}")
        if result:
            statuses = result.get("_embedded", {}).get("statuses", [])
            # Находим статус с минимальным sort (это первый статус)
            if statuses:
                first_status = min(statuses, key=lambda x: x.get("sort", 999999))
                return first_status.get("id")
        return None
    
    async def get_deal(self, deal_id: int) -> Optional[Dict]:
        """Get deal by ID."""
        return await self._request("GET", f"/leads/{deal_id}")
    
    async def get_custom_fields(self) -> Optional[Dict]:
        """Get all custom fields for leads (to find field IDs)."""
        return await self._request("GET", "/leads/custom_fields")
    
    async def get_pipelines(self) -> Optional[Dict]:
        """Get all pipelines (to find pipeline ID)."""
        return await self._request("GET", "/leads/pipelines")
    
    async def add_note(self, deal_id: int, text: str) -> bool:
        """
        Add a note (comment) to a deal.
        Used to attach conversation history.
        """
        note_data = [{
            "entity_id": deal_id,
            "note_type": "common",
            "params": {
                "text": text
            }
        }]
        
        result = await self._request("POST", "/leads/notes", json=note_data)
        if result:
            logger.info(f"Added note to deal {deal_id}")
            return True
        return False
    
    async def create_task(self, deal_id: int, text: str, responsible_user_id: int = None) -> bool:
        """
        Create a task linked to a deal (e.g., 'Call back client').
        This creates a notification for managers.
        
        Args:
            deal_id: AmoCRM deal ID
            text: Task text (e.g., "Клиент просит изменить дату праздника")
            responsible_user_id: Optional user ID to assign task to
        """
        import time
        
        # Task deadline: 1 hour from now
        complete_till = int(time.time()) + 3600
        
        task_data = [{
            "entity_id": deal_id,
            "entity_type": "leads",
            "text": text,
            "complete_till": complete_till,
            "task_type_id": 1  # 1 = "Связаться с клиентом" (Call)
        }]
        
        if responsible_user_id:
            task_data[0]["responsible_user_id"] = responsible_user_id
        
        result = await self._request("POST", "/tasks", json=task_data)
        if result:
            logger.info(f"Created task for deal {deal_id}: {text}")
            return True
        logger.error(f"Failed to create task for deal {deal_id}")
        return False
    
    async def is_deal_in_work(self, deal_id: int) -> bool:
        """
        Check if deal has been taken into work (status != first 2 statuses).
        Returns True if deal is in 'Взято в работу' or later.
        """
        deal = await self.get_deal(deal_id)
        if not deal:
            return False
        
        current_status_id = deal.get("status_id")
        pipeline_id = deal.get("pipeline_id")
        
        allowed_status_ids = await self._get_allowed_status_ids(pipeline_id)
        
        # If current status is NOT in allowed (first 2), then it's "in work"
        return current_status_id not in allowed_status_ids
    
    @property
    def is_authorized(self) -> bool:
        """Check if we have valid tokens."""
        return bool(self._access_token)
    
    def get_auth_url(self) -> str:
        """Get authorization URL for initial OAuth2 flow."""
        from urllib.parse import quote
        redirect = quote(self.redirect_uri or "", safe="")
        return (
            f"{self.base_url}/oauth"
            f"?client_id={self.client_id}"
            f"&redirect_uri={redirect}"
            f"&response_type=code"
            f"&mode=post_message"
        )


# Global instance
amocrm_client = AmoCRMClient()


async def send_lead_to_amocrm(lead_data: Dict[str, Any], telegram_id: int = None, username: str = None, vk_id: int = None) -> Optional[int]:
    """
    Convenience function to send a lead to AmoCRM.
    Uses find_or_create_contact for contact merging by phone.
    
    Args:
        lead_data: dict with keys: customer_name, phone, child_name, event_date, etc.
        telegram_id: Telegram user ID to save in contact
        username: Telegram username
        vk_id: VK user ID to save in contact
    
    Returns:
        Tuple (deal_id, contact_id) or (None, None)
    """
    if not amocrm_client.is_authorized:
        logger.warning("AmoCRM not authorized, skipping")
        return None, None
    
    try:
        phone = lead_data.get("phone", "")
        customer_name = lead_data.get("customer_name") or lead_data.get("child_name") or "Клиент"
        
        # Use find_or_create_contact for merging by phone
        contact_id = await amocrm_client.find_or_create_contact({
            "name": customer_name,
            "phone": phone,
            "telegram_id": telegram_id,
            "username": username,
            "vk_id": vk_id
        })
        
        if not contact_id:
            logger.error("Failed to get/create contact")
            return None, None
        
        # Create deal
        deal_id = await amocrm_client.create_deal(contact_id, lead_data)
        
        return deal_id, contact_id
        
    except Exception as e:
        logger.error(f"Error sending lead to AmoCRM: {e}")
        return None, None
