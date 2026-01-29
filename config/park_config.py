"""
Мультипарковая конфигурация сети Джунгли Сити.

Архитектура:
- base_config.json — общие настройки сети (промты, праздники, общие правила)  
- park_{id}.json — настройки конкретного парка (цены, контакты, расписание)

При загрузке конфиг парка объединяется с базовым: park overrides base.

PARK_ID определяется через env-переменную или передаётся явно.
"""

import json
import os
from pathlib import Path
from typing import Optional
from functools import lru_cache

# ============ ПУТИ ============

DATA_DIR = Path(__file__).parent.parent / "data"
BASE_CONFIG_FILE = DATA_DIR / "base_config.json"

def get_park_config_file(park_id: str) -> Path:
    """Путь к конфигу конкретного парка."""
    return DATA_DIR / f"park_{park_id}.json"


# ============ ТЕКУЩИЙ PARK_ID ============

def get_current_park_id() -> str:
    """
    Получить ID текущего парка.
    Приоритет: env PARK_ID > default "nn"
    """
    return os.getenv("PARK_ID", "nn")


# ============ БАЗОВЫЙ КОНФИГ СЕТИ ============

BASE_CONFIG_DEFAULT = {
    "network": {
        "name": "Джунгли Сити",
        "brand": "Jungle City"
    },
    
    "holidays": [
        # Общие праздники для всех парков
        {"month": 1, "days": [1, 2, 3, 4, 5, 6, 7, 8]},  # Новогодние
        {"month": 2, "days": [23]},                       # 23 февраля
        {"month": 3, "days": [8]},                        # 8 марта
        {"month": 5, "days": [1, 9]},                     # Майские
        {"month": 6, "days": [12]},                       # День России
        {"month": 11, "days": [4]}                        # День народного единства
    ],
    
    "discounts": {
        # Общие скидки сети
        "kids_1_4_weekdays": 20,
        "large_family": 30,
        "after_20": 50,
        "birthday_person": 50,
        "disabled_weekdays": 100,
        "svo_children": 30
    },
    
    "birthday": {
        # Общие правила ДР для сети
        "min_kids": 6,
        "free_birthday_from": 7,
        "room_duration_hours": 3,
        "slots": ["10:30", "14:30", "18:30"],
        "cake_fee": 1000,
        "min_deposit": 1000
    },
    
    "extras": {
        "phygital": 200,
        "vr_10min": 350,
        "vr_30min": 1000,
        "vr_60min": 2000
    }
}


# ============ ДЕФОЛТ ДЛЯ ПАРКА (Нижний Новгород) ============

PARK_CONFIG_DEFAULT = {
    "park_id": "nn",
    
    "park": {
        "name": "Джунгли Сити",
        "city": "Нижний Новгород",
        "address": "ул. Коминтерна, 11, ТЦ «Лента», 1 этаж",
        "metro": "Буревестник — 250 м"
    },
    
    "phones": {
        "main": "+7 (831) 213-50-50",
        "whatsapp": "+7 (962) 509-74-93"
    },
    
    "schedule": {
        "monday": {"open": "12:00", "close": "22:00"},
        "weekdays": {"open": "10:00", "close": "22:00"},
        "weekends": {"open": "10:00", "close": "22:00"},
        "entrance_until": "21:00",
        "restaurant_until": "21:00",
        "birthday_dept_until": "21:00"
    },
    
    "prices": {
        "monday": 990,
        "weekday": 1190,
        "weekend": 1590,
        "adults_free": True,
        "under_1_free": True
    },
    
    "links": {
        "site": "https://nn.jucity.ru/",
        "afisha": "https://nn.jucity.ru/afisha/",
        "rules": "https://nn.jucity.ru/rules/",
        "menu": "https://catalog.botcicada.ru/menu.html",
        "specmenu": "https://catalog.botcicada.ru/specmenu.html",
        "cakes": "https://catalog.botcicada.ru/cakes.html",
        "animators": "https://catalog.botcicada.ru/animators.html",
        "telegram": "https://t.me/juicitynn",
        "instagram": "@juicitynn"
    },
    
    "amocrm": {
        "pipeline_id": None,  # ID воронки для этого парка
        "responsible_user_id": None
    }
}


# ============ ЗАГРУЗКА И СОХРАНЕНИЕ ============

def _load_json(path: Path, default: dict) -> dict:
    """Загрузить JSON или вернуть default."""
    if path.exists():
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return default.copy()


def _save_json(path: Path, data: dict) -> bool:
    """Сохранить данные в JSON."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except IOError:
        return False


def _deep_merge(base: dict, override: dict) -> dict:
    """Глубокое объединение словарей: override перезаписывает base."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_base_config() -> dict:
    """Загрузить базовый конфиг сети."""
    return _load_json(BASE_CONFIG_FILE, BASE_CONFIG_DEFAULT)


def save_base_config(config: dict) -> bool:
    """Сохранить базовый конфиг сети."""
    return _save_json(BASE_CONFIG_FILE, config)


def load_park_config(park_id: Optional[str] = None) -> dict:
    """Загрузить конфиг конкретного парка (без базового)."""
    park_id = park_id or get_current_park_id()
    config_file = get_park_config_file(park_id)
    default = PARK_CONFIG_DEFAULT.copy()
    default["park_id"] = park_id
    return _load_json(config_file, default)


def save_park_config(config: dict, park_id: Optional[str] = None) -> bool:
    """Сохранить конфиг парка."""
    park_id = park_id or config.get("park_id") or get_current_park_id()
    config_file = get_park_config_file(park_id)
    return _save_json(config_file, config)


def load_config(park_id: Optional[str] = None) -> dict:
    """
    Загрузить полный конфиг: base + park.
    Данные парка перезаписывают базовые.
    """
    base = load_base_config()
    park = load_park_config(park_id)
    return _deep_merge(base, park)


def save_config(config: dict, park_id: Optional[str] = None) -> bool:
    """
    Сохранить конфиг парка.
    Для обратной совместимости — сохраняет только park-специфичные данные.
    """
    return save_park_config(config, park_id)


# ============ ХЕЛПЕРЫ ДЛЯ ПОЛУЧЕНИЯ ДАННЫХ ============

def get_prices(park_id: Optional[str] = None) -> dict:
    """Получить цены на билеты."""
    config = load_config(park_id)
    return config.get("prices", PARK_CONFIG_DEFAULT["prices"])


def get_schedule(park_id: Optional[str] = None) -> dict:
    """Получить расписание работы."""
    config = load_config(park_id)
    return config.get("schedule", PARK_CONFIG_DEFAULT["schedule"])


def get_phones(park_id: Optional[str] = None) -> dict:
    """Получить телефоны."""
    config = load_config(park_id)
    return config.get("phones", PARK_CONFIG_DEFAULT["phones"])


def get_birthday_config(park_id: Optional[str] = None) -> dict:
    """Получить настройки дней рождения."""
    config = load_config(park_id)
    return config.get("birthday", BASE_CONFIG_DEFAULT["birthday"])


def get_links(park_id: Optional[str] = None) -> dict:
    """Получить ссылки."""
    config = load_config(park_id)
    return config.get("links", PARK_CONFIG_DEFAULT["links"])


def get_discounts(park_id: Optional[str] = None) -> dict:
    """Получить скидки."""
    config = load_config(park_id)
    return config.get("discounts", BASE_CONFIG_DEFAULT["discounts"])


def get_holidays(park_id: Optional[str] = None) -> list:
    """Получить список праздничных дней."""
    config = load_config(park_id)
    return config.get("holidays", BASE_CONFIG_DEFAULT["holidays"])


def get_park_info(park_id: Optional[str] = None) -> dict:
    """Получить информацию о парке (название, город, адрес)."""
    config = load_config(park_id)
    return config.get("park", PARK_CONFIG_DEFAULT["park"])


# ============ УТИЛИТЫ ============

def is_holiday(month: int, day: int, park_id: Optional[str] = None) -> bool:
    """Проверить, является ли день праздничным."""
    holidays = get_holidays(park_id)
    for h in holidays:
        if h["month"] == month and day in h["days"]:
            return True
    return False


def get_price_for_day(weekday: int, month: int = None, day: int = None, park_id: Optional[str] = None) -> int:
    """
    Получить цену билета для дня недели.
    weekday: 0=понедельник, 6=воскресенье
    """
    prices = get_prices(park_id)
    
    # Проверяем праздники
    if month and day and is_holiday(month, day, park_id):
        return prices["weekend"]
    
    if weekday == 0:  # Понедельник
        return prices["monday"]
    elif weekday < 5:  # Вторник-пятница
        return prices["weekday"]
    else:  # Суббота-воскресенье
        return prices["weekend"]


def list_parks() -> list[str]:
    """Получить список всех парков (по файлам конфигов)."""
    parks = []
    if DATA_DIR.exists():
        for f in DATA_DIR.glob("park_*.json"):
            park_id = f.stem.replace("park_", "")
            parks.append(park_id)
    if not parks:
        parks = ["nn"]  # Дефолтный парк
    return sorted(parks)


# ============ ОБРАТНАЯ СОВМЕСТИМОСТЬ ============

# Для кода, который использует старый PARK_CONFIG
PARK_CONFIG = load_config()
