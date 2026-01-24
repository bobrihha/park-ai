import re
from pathlib import Path
from datetime import datetime, timedelta, date
from typing import Optional

def get_prices_from_knowledge(park_id: str = "nn") -> dict:
    """
    Парсит файл prices.txt и возвращает словарь с ценами.
    Если не находит, возвращает дефолтные значения.
    """
    default_prices = {
        "monday": 990,
        "weekday": 1190,
        "weekend": 1590
    }
    
    try:
        # Путь к файлу цен
        root = Path(__file__).parent.parent
        file_path = root / "knowledge" / park_id / "general" / "prices.txt"
        
        if not file_path.exists():
            return default_prices
            
        content = file_path.read_text(encoding="utf-8")
        
        prices = default_prices.copy()
        
        # Понедельник
        monday_match = re.search(r"Понедельник[^:]*:.*?(\d+)\s*руб", content, re.IGNORECASE)
        if monday_match:
            prices["monday"] = int(monday_match.group(1))
            
        # Будни
        weekday_match = re.search(r"Будни[^:]*:.*?(\d+)\s*руб", content, re.IGNORECASE)
        if weekday_match:
            prices["weekday"] = int(weekday_match.group(1))
            
        # Выходные
        weekend_match = re.search(r"Выходные[^:]*:.*?(\d+)\s*руб", content, re.IGNORECASE)
        if weekend_match:
            prices["weekend"] = int(weekend_match.group(1))
            
        return prices
        
    except Exception as e:
        print(f"Error parsing prices: {e}")
        return default_prices

def get_prices_text(park_id: str = "nn") -> str:
    """Возвращает полное содержимое файла цен."""
    try:
        root = Path(__file__).parent.parent
        file_path = root / "knowledge" / park_id / "general" / "prices.txt"
        if file_path.exists():
            return file_path.read_text(encoding="utf-8")
    except:
        pass
    return ""


def format_phone(phone: str) -> str:
    """Форматировать телефон для отображения."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) == 11 and digits[0] in ("7", "8"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"+7 ({digits[0:3]}) {digits[3:6]}-{digits[6:8]}-{digits[8:10]}"
    return str(phone)


def get_afisha_events(park_id: str = "nn") -> str:
    """
    Парсит файл afisha.txt и возвращает красивый текст с событиями.
    """
    # Эмодзи для разных типов событий
    event_emoji = {
        "мастер-класс": "✨",
        "бармен": "🍹",
        "кулинар": "👨‍🍳",
        "розыгрыш": "🎁",
        "лото": "🎵",
        "именинник": "🎂",
        "дискотека": "💃",
        "шоу": "🌟",
    }
    
    try:
        root = Path(__file__).parent.parent
        file_path = root / "knowledge" / park_id / "events" / "afisha.txt"
        
        if not file_path.exists():
            return None
        
        content = file_path.read_text(encoding="utf-8")
        
        # Парсим события из файла
        # Формат: 📅 DD.MM.YYYY в HH:MM\n🎪 Название
        events = []
        lines = content.split("\n")
        
        current_date = None
        for line in lines:
            line = line.strip()
            
            # Ищем дату: 📅 13.01.2026 в 18:00
            date_match = re.search(r"📅\s*(\d{1,2})\.(\d{1,2})\.\d{4}\s*в\s*(\d{1,2}:\d{2})", line)
            if date_match:
                day = date_match.group(1)
                month = int(date_match.group(2))
                time = date_match.group(3)
                
                # Месяцы на русском
                months = ["", "января", "февраля", "марта", "апреля", "мая", "июня",
                          "июля", "августа", "сентября", "октября", "ноября", "декабря"]
                month_name = months[month] if month <= 12 else str(month)
                
                current_date = f"{day} {month_name}, {time}"
                continue
            
            # Ищем название события: 🎪 Название
            event_match = re.search(r"🎪\s*(.+)", line)
            if event_match and current_date:
                event_name = event_match.group(1).strip()
                
                # Подбираем подходящий эмодзи
                emoji = "🎪"
                for keyword, em in event_emoji.items():
                    if keyword in event_name.lower():
                        emoji = em
                        break
                
                events.append(f"{emoji} {current_date} — {event_name}")
                current_date = None
        
        if not events:
            return None
        
        # Формируем красивый текст
        events_text = "\n".join(events)
        result = (
            f"🎪 Ближайшие события в Джунгли Сити!\n\n"
            f"{events_text}\n\n"
            f"Приходите — будет весело! 🎉\n\n"
            f"👉 Полная афиша: nn.jucity.ru/afisha/"
        )
        return result
        
    except Exception as e:
        print(f"Error parsing afisha: {e}")
        return None


# ====== Date helpers for birthday flow ======

_MONTHS_RU_TO_NUM = {
    "января": 1, "янв": 1,
    "февраля": 2, "фев": 2,
    "марта": 3, "мар": 3,
    "апреля": 4, "апр": 4,
    "мая": 5, "май": 5,
    "июня": 6, "июн": 6,
    "июля": 7, "июл": 7,
    "августа": 8, "авг": 8,
    "сентября": 9, "сен": 9, "сент": 9,
    "октября": 10, "окт": 10,
    "ноября": 11, "ноя": 11,
    "декабря": 12, "дек": 12,
}

_MONTHS_NUM_TO_RU = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}

_WEEKDAYS_RU = [
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
]

_HOLIDAYS = {
    (1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7), (1, 8),
    (2, 23),
    (3, 8),
    (5, 1), (5, 9),
    (6, 12),
    (11, 4),
}


def _next_weekday(start_date: date, target_weekday: int) -> date:
    days_ahead = (target_weekday - start_date.weekday()) % 7
    return start_date + timedelta(days=days_ahead)


def parse_user_date(text: str, now: datetime | None = None) -> date | None:
    """Parse user message and return a date if possible."""
    if not text:
        return None

    now_dt = now or datetime.now()
    today = now_dt.date()
    t = text.lower()

    if "послезавтра" in t:
        return today + timedelta(days=2)
    if "завтра" in t:
        return today + timedelta(days=1)
    if "сегодня" in t:
        return today

    # Day of week (e.g. "в субботу")
    for idx, name in enumerate(_WEEKDAYS_RU):
        if re.search(rf"\\b{name}\\b", t):
            return _next_weekday(today, idx)

    # "на выходных"
    if "выходн" in t:
        if today.weekday() in (5, 6):
            return today
        return _next_weekday(today, 5)

    # Numeric formats: dd.mm.yyyy, dd/mm/yyyy, dd-mm-yyyy
    match = re.search(r"(\\d{1,2})[./-](\\d{1,2})[./-](\\d{4})", t)
    if match:
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        try:
            return date(year, month, day)
        except ValueError:
            return None

    # Numeric formats without year: dd.mm or dd/mm or dd-mm
    match = re.search(r"(\\d{1,2})[./-](\\d{1,2})", t)
    if match:
        day, month = int(match.group(1)), int(match.group(2))
        year = today.year
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        if candidate < today:
            try:
                candidate = date(year + 1, month, day)
            except ValueError:
                return None
        return candidate

    # "25 января" / "25 янв 2026"
    match = re.search(r"(\\d{1,2})\\s*([а-яё]+)(?:\\s*(\\d{4}))?", t)
    if match:
        day = int(match.group(1))
        month_name = match.group(2)
        year = int(match.group(3)) if match.group(3) else today.year
        month = _MONTHS_RU_TO_NUM.get(month_name)
        if month:
            try:
                candidate = date(year, month, day)
            except ValueError:
                return None
            if not match.group(3) and candidate < today:
                try:
                    candidate = date(year + 1, month, day)
                except ValueError:
                    return None
            return candidate

    return None


def format_date_ru(d: date, include_year: bool = False) -> str:
    month_name = _MONTHS_NUM_TO_RU.get(d.month, str(d.month))
    if include_year:
        return f"{d.day} {month_name} {d.year}"
    return f"{d.day} {month_name}"


def format_weekday_ru(d: date, capitalize: bool = False) -> str:
    name = _WEEKDAYS_RU[d.weekday()]
    return name.capitalize() if capitalize else name


def is_holiday(d: date) -> bool:
    return (d.month, d.day) in _HOLIDAYS


def get_birthday_price_for_date(d: date, prices: dict | None = None) -> int:
    prices = prices or get_prices_from_knowledge()
    if is_holiday(d):
        return prices["weekend"]
    if d.weekday() == 0:
        return prices["monday"]
    if d.weekday() in (5, 6):
        return prices["weekend"]
    return prices["weekday"]


def build_birthday_date_question(d: date, prices: dict | None = None) -> str:
    price = get_birthday_price_for_date(d, prices)
    date_str = format_date_ru(d, include_year=True)
    weekday = format_weekday_ru(d, capitalize=False)
    return (
        f"📅 {date_str} — это {weekday}, цена детского билета {price} ₽.\n\n"
        "👶 Сколько детей будет всего, включая именинника?"
    )


def parse_kids_count(text: str, max_count: int = 60) -> Optional[int]:
    """Extract kids count from user message (short numeric responses)."""
    if not text:
        return None

    t = text.lower().strip()

    # Avoid time like 10:30
    if re.search(r"\b\d{1,2}[:.]\d{2}\b", t):
        return None

    # Avoid phone-like numbers (10-11 digits)
    if re.search(r"\b[789]\d{9,10}\b", t):
        return None

    # Pure digits (1-2 digits)
    if re.fullmatch(r"\d{1,2}", t):
        n = int(t)
        return n if 1 <= n <= max_count else None

    # Patterns with "дет" or "реб"
    m = re.search(r"(\d{1,2})\s*(дет|реб)", t)
    if m:
        n = int(m.group(1))
        return n if 1 <= n <= max_count else None

    m = re.search(r"(дет|реб)[^\d]{0,10}(\d{1,2})", t)
    if m:
        n = int(m.group(2))
        return n if 1 <= n <= max_count else None

    return None


def filter_extras_from_message(
    message: str,
    extras: list[str] | None,
    last_bot_message: str | None = None,
) -> list[str]:
    """Filter extras so they are added only on explicit order or confirmed add."""
    if not extras:
        return []

    text = (message or "").lower()
    bot_text = (last_bot_message or "").lower()

    # Explicit order intent words
    order_keywords = [
        "заказать", "закажу", "закажем", "закажите",
        "хочу", "хотим",
        "нужен", "нужна", "нужно",
        "добавьте", "добавить",
        "возьмем", "возьмём", "берем", "берём",
        "оформить", "оформим",
    ]

    # Question/permission patterns -> not an order
    question_block = [
        "можно", "можно ли", "разрешено",
        "нужен ли", "нужна ли", "нужно ли",
        "свой торт", "со своим тортом", "принести торт", "принесем торт", "принесём торт",
        "торт с собой", "торт с собой можно",
    ]

    simple_yes = text.strip() in ["да", "да!", "ага", "ок", "окей", "конечно", "давайте", "давай", "хочу"]
    has_order_intent = any(k in text for k in order_keywords)
    has_question = any(k in text for k in question_block)

    if has_question:
        return []

    # Extras explicitly mentioned by user
    mentioned = [e for e in extras if e and e.lower() in text]
    if mentioned and (has_order_intent or simple_yes):
        return mentioned

    # Allow confirmation like "да/закажите" if bot just asked to add a specific extra
    if (has_order_intent or simple_yes) and bot_text:
        bot_has_order_prompt = any(k in bot_text for k in ["добав", "заказ", "оформ"])
        if bot_has_order_prompt:
            bot_mentions = [e for e in extras if e and e.lower() in bot_text]
            if bot_mentions:
                return bot_mentions

    return []


def should_defer_phone_request(message: str) -> bool:
    """Return True if user asks for info and we should answer before requesting phone."""
    if not message:
        return False

    text = message.lower().strip()

    # Don't defer if this looks like a phone number
    if re.search(r"\b[789]\d{9,10}\b", text) or re.search(r"[\+\(\)]", text):
        return False

    info_triggers = [
        "расскажи", "расскажите", "подскажите", "есть ли", "можно ли",
        "что входит", "сколько", "какие", "какой", "цена", "стоимость",
    ]
    topic_keywords = [
        "пакет", "под ключ", "аниматор", "анимац", "квест", "шоу",
        "торт", "шар", "аквагрим", "фотограф", "меню", "еда", "угощ",
        "комната", "ресторан", "слот", "время", "формат",
    ]

    has_info_trigger = any(k in text for k in info_triggers) or "?" in text
    has_topic = any(k in text for k in topic_keywords)

    return has_info_trigger and has_topic


def extract_phone_from_message(message: str) -> str | None:
    """Extract a phone number from a message (returns last 10 digits)."""
    if not message:
        return None
    digits = re.sub(r"\D", "", str(message))
    if len(digits) < 10:
        return None
    return digits[-10:]


def extract_format_from_message(message: str) -> str | None:
    """Extract party format from user message."""
    if not message:
        return None
    text = message.lower()

    restaurant_keywords = [
        "ресторан", "в ресторане", "зал ресторана",
        "столик", "столик в ресторане", "стол",
    ]
    room_keywords = [
        "комната", "комнату", "комнатка",
        "тематическ", "room",
    ]

    if any(k in text for k in restaurant_keywords):
        return "Ресторан"
    if any(k in text for k in room_keywords):
        return "Тематическая комната"
    return None
