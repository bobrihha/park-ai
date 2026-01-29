"""Intent Router — определение намерения пользователя."""

import re
from dataclasses import dataclass
from openai import OpenAI

from config.settings import OPENAI_API_KEY


@dataclass
class IntentResult:
    """Результат определения намерения."""
    intent: str  # birthday, general, events, unknown
    confidence: float
    reason: str


# Триггеры для ДР/праздников
BIRTHDAY_TRIGGERS = [
    r'\bдр\b', r'день\s*рождени', r'праздник', r'аниматор', r'торт[аыуе]?\b',
    r'домик', r'комнат[аыуе]', r'банкет', r'выпускн', r'бронь', r'предбронь',
    r'каталог', r'шар[аыи]', r'именинник', r'отметить', r'отпраздн', r'меропри',
    r'заказать\s*праздник', r'организовать', r'программ[ауы]', r'аквагрим',
    r'фото\s*сесси', r'украс', r'оформлен', r'вечеринк'
]

# Триггеры для общих вопросов
GENERAL_TRIGGERS = [
    r'цен[аыу]', r'сколько\s*стоит', r'билет', r'режим', r'работа[ею]т',
    r'адрес', r'как\s*доехать', r'добраться', r'правил', r'носк[иа]',
    r'скидк', r'акци[яию]', r'аттракцион', r'горк[аиу]', r'батут',
    r'ресторан', r'кафе', r'меню', r'покушать', r'поесть', r'vr', r'виртуальн',
    r'парков[ку]', r'метро', r'время', r'часы', r'когда', r'пройти', r'войти',
    r'возраст', r'малыш', r'взросл[ыа]', r'роди[тел]', r'бесплатн',
    r'многодет', r'инвалид', r'льгот', r'подарочн', r'сертификат'
]

# Триггеры для афиши/событий
EVENTS_TRIGGERS = [
    r'афиша', r'событи[яею]', r'мероприяти[яею]', r'расписани[ею]',
    r'что\s*будет', r'что\s*проходит', r'какие\s*праздник', r'ближайши[ех]',
    r'на\s*выходн', r'в\s*субботу', r'в\s*воскресен', r'сегодня', r'завтра',
    r'шоу\b', r'представлен', r'концерт', r'дискотек', r'анонс'
]

# Триггеры для потерянных вещей
LOST_ITEM_TRIGGERS = [
    r'потеря[лаи]', r'потерял[аи]?', r'пропал[аои]?', r'утеря[нл]',
    r'забыл[аи]?\s*(\w+\s+)?в\s*парке', r'оставил[аи]?\s*(\w+\s+)?в\s*парке',
    r'бюро\s*находок', r'потерянн', r'потеряшк',
    r'не\s*могу\s*найти', r'не\s*наш[ёлла]',
    r'забыл[аи]?\s*вещ', r'оставил[аи]?\s*вещ'
]


def detect_intent_rules(message: str) -> IntentResult | None:
    """Определить намерение по правилам (быстро, без API)."""
    text = message.lower()
    
    birthday_score = 0
    general_score = 0
    
    # Проверяем триггеры ДР
    for trigger in BIRTHDAY_TRIGGERS:
        if re.search(trigger, text):
            birthday_score += 1
    
    # Проверяем триггеры общих вопросов
    for trigger in GENERAL_TRIGGERS:
        if re.search(trigger, text):
            general_score += 1
    
    # Проверяем триггеры событий
    events_score = 0
    for trigger in EVENTS_TRIGGERS:
        if re.search(trigger, text):
            events_score += 1
    
    # Проверяем триггеры потеряшек
    lost_item_score = 0
    for trigger in LOST_ITEM_TRIGGERS:
        if re.search(trigger, text):
            lost_item_score += 1
    
    # Определяем победителя
    scores = {'birthday': birthday_score, 'general': general_score, 'events': events_score, 'lost_item': lost_item_score}
    max_intent = max(scores, key=scores.get)
    max_score = scores[max_intent]
    
    if max_score >= 1:
        if max_intent == 'birthday':
            return IntentResult(
                intent="birthday",
                confidence=min(0.7 + max_score * 0.1, 0.95),
                reason=f"Найдено {max_score} триггеров праздника"
            )
        elif max_intent == 'events':
            return IntentResult(
                intent="events",
                confidence=min(0.7 + max_score * 0.1, 0.95),
                reason=f"Найдено {max_score} триггеров афиши"
            )
        elif max_intent == 'lost_item':
            return IntentResult(
                intent="lost_item",
                confidence=min(0.8 + max_score * 0.1, 0.95),
                reason=f"Найдено {max_score} триггеров потеряшек"
            )
        else:
            return IntentResult(
                intent="general",
                confidence=min(0.6 + max_score * 0.1, 0.9),
                reason=f"Найдено {max_score} триггеров общих вопросов"
            )
    
    return None  # Не удалось определить правилами


def detect_intent_llm(message: str, history: list[dict] = None) -> IntentResult:
    """Определить намерение через LLM (fallback)."""
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    prompt = f"""Определи намерение пользователя чата парка развлечений "Джунгли Сити".

Сообщение пользователя: "{message}"

Варианты:
- birthday — если человек хочет организовать праздник, день рождения, выпускной, спрашивает про аниматоров, торты, комнаты, бронирование
- general — если хочет узнать о посещении парка: цены, режим, правила, аттракционы, скидки, как добраться
- lost_item — если человек потерял вещь в парке, забыл, оставил что-то, спрашивает про бюро находок
- unknown — если непонятно (приветствие, нейтральный вопрос)

Ответь ТОЛЬКО одним словом: birthday, general, lost_item или unknown"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",  # Быстрая дешёвая модель для классификации
        messages=[{"role": "user", "content": prompt}],
        max_tokens=10,
        temperature=0
    )
    
    result = response.choices[0].message.content.strip().lower()
    
    if result in ["birthday", "general", "lost_item", "unknown"]:
        return IntentResult(intent=result, confidence=0.7, reason="LLM классификация")
    
    return IntentResult(intent="unknown", confidence=0.5, reason="LLM вернул неожиданный ответ")


def detect_intent(message: str, history: list[dict] = None) -> IntentResult:
    """Определить намерение пользователя (правила → LLM fallback)."""
    # Сначала пробуем правила (быстро и бесплатно)
    result = detect_intent_rules(message)
    if result:
        return result
    
    # Если правила не сработали — спрашиваем LLM
    try:
        return detect_intent_llm(message, history)
    except Exception as e:
        print(f"LLM intent detection error: {e}")
        return IntentResult(intent="unknown", confidence=0.3, reason=f"Ошибка LLM: {e}")
