"""AI Agent — основной модуль общения с пользователем."""

from datetime import datetime
from openai import OpenAI

from config.settings import OPENAI_API_KEY, OPENAI_MODEL
from config.prompts import get_system_prompt


class Agent:
    """AI агент для общения с пользователями."""
    
    def __init__(self):
        self.client = OpenAI(api_key=OPENAI_API_KEY)
        self.model = OPENAI_MODEL
    
    def generate_response(
        self,
        message: str,
        intent: str,
        history: list[dict] = None,
        rag_context: str = None,
        lead_data: dict = None,
        deal_in_work: bool = False
    ) -> str:
        """
        Сгенерировать ответ на сообщение пользователя.
        
        Args:
            message: Сообщение пользователя
            intent: Намерение (birthday, general, unknown)
            history: История сообщений
            rag_context: Контекст из базы знаний (RAG)
            lead_data: Уже собранные данные лида
        
        Returns:
            Ответ бота
        """
        # Формируем системный промпт
        system_prompt = get_system_prompt(intent)
        
        # Добавляем ТЕКУЩУЮ ДАТУ (чтобы бот знал день недели)
        now = datetime.now()
        days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        current_date_str = now.strftime("%d.%m.%Y")
        current_day = days[now.weekday()]
        
        system_prompt += f"\n\n📅 СЕГОДНЯ: {current_date_str} ({current_day})"
        system_prompt += f"\nТекщий год: {now.year}"

        
        # Добавляем контекст из базы знаний
        if rag_context:
            system_prompt += f"\n\n--- ИНФОРМАЦИЯ ИЗ БАЗЫ ЗНАНИЙ ---\n{rag_context}\n---"
        
        # Добавляем контекст собранного лида (для birthday ветки)
        if intent == "birthday" and lead_data:
            # НОВАЯ ЛОГИКА: Показываем структурированные данные как ИСТОЧНИК ИСТИНЫ
            system_prompt += "\n\n" + "="*50
            system_prompt += "\n📋 ТЕКУЩЕЕ СОСТОЯНИЕ ЗАЯВКИ (ИСТОЧНИК ИСТИНЫ)"
            system_prompt += "\n" + "="*50
            system_prompt += "\n\nИСПОЛЬЗУЙ ЭТИ ДАННЫЕ при подтверждении! НЕ ФАНТАЗИРУЙ!\n"
            
            # Основные данные
            system_prompt += f"\n👤 Имя для связи: {lead_data.get('customer_name') or '❌ НЕ УКАЗАНО'}"
            system_prompt += f"\n📞 Телефон: {lead_data.get('phone') or '❌ НЕ УКАЗАН'}"
            system_prompt += f"\n📅 Дата праздника: {lead_data.get('event_date') or '❌ НЕ УКАЗАНА'}"
            system_prompt += f"\n⏰ Время: {lead_data.get('time') or '❌ НЕ УКАЗАНО'}"
            
            # Гости
            kids = lead_data.get('kids_count')
            adults = lead_data.get('adults_count')
            system_prompt += f"\n👶 Детей: {kids if kids else '❌ НЕ УКАЗАНО'}"
            system_prompt += f"\n👨 Взрослых: {adults if adults else '❌ НЕ УКАЗАНО'}"
            
            # Именинник (опционально)
            child_name = lead_data.get('child_name')
            child_age = lead_data.get('child_age')
            if child_name:
                system_prompt += f"\n🎂 Именинник: {child_name}"
                if child_age:
                    system_prompt += f", {child_age} лет"
            
            # Комната и формат
            room = lead_data.get('room')
            format_type = lead_data.get('format')
            if room:
                system_prompt += f"\n🏠 Комната: {room}"
            if format_type:
                system_prompt += f"\n🎪 Формат: {format_type}"
            
            # Доп услуги
            extras = lead_data.get('extras', [])
            if extras and extras != '[]':
                try:
                    import json
                    if isinstance(extras, str):
                        extras = json.loads(extras)
                    if extras:
                        system_prompt += f"\n✨ Дополнительно: {', '.join(extras)}"
                except:
                    pass
            
            system_prompt += "\n" + "="*50

            system_prompt += (
                "\n\n🚫 ВАЖНО: НЕ сообщай о принятии/передаче заявки сразу после телефона."
                "\nТелефон нужен для CRM, но клиенту это НЕ озвучиваем на этом шаге."
                "\nСначала собери: дата, дети, телефон, формат, время (если комната), имя."
                "\nДаже если заявка уже создана в CRM — продолжай квалификацию и задавай следующий вопрос."
            )
            
            # Определяем что ещё нужно собрать (ПОРЯДОК ВАЖЕН!)
            # После дата + дети + телефон → создаём заявку в CRM и продолжаем собирать данные
            missing = []
            if not lead_data.get("event_date"):
                missing.append("Дата праздника")
            if not lead_data.get("kids_count"):
                missing.append("Количество детей (включая именинника)")
            if not lead_data.get("phone"):
                missing.append("Номер телефона для связи")
            # После получения телефона — заявка уходит в CRM, но мы продолжаем собирать данные
            format_value = (lead_data.get("format") or "").strip().lower()
            if not lead_data.get("format"):
                missing.append("Формат праздника (Тематическая комната или Ресторан)")
            is_room = "комнат" in format_value or "room" in format_value
            if is_room and not lead_data.get("time"):
                missing.append("Время начала (10:30, 14:30 или 18:30)")
            if not lead_data.get("customer_name"):
                missing.append("Имя для связи")
            
            if missing:
                # Указываем СЛЕДУЮЩИЙ КОНКРЕТНЫЙ вопрос
                next_question = missing[0]
                system_prompt += f"\n\n🔴 СЛЕДУЮЩИЙ ВОПРОС, КОТОРЫЙ ТЫ ОБЯЗАН ЗАДАТЬ:\n→ {next_question}\n"
                system_prompt += f"\nЕЩЁ НУЖНО УЗНАТЬ: {', '.join(missing[1:]) if len(missing) > 1 else 'ничего'}"
                system_prompt += "\n\nНЕ ОТВЛЕКАЙСЯ на акции и каталоги пока не соберёшь ВСЕ данные!"
            else:
                system_prompt += "\n\n✅ ВСЕ ОБЯЗАТЕЛЬНЫЕ ДАННЫЕ СОБРАНЫ!"
                system_prompt += "\n\n📝 ТВОЯ ЗАДАЧА:"
                system_prompt += "\n1. Сформируй красивое подтверждение ИСПОЛЬЗУЯ данные выше"
                system_prompt += "\n2. Укажи точную стоимость (рассчитай по ценам из базы знаний)"
                system_prompt += "\n3. Спроси: 'Всё верно? Могу ли я передать заявку на бронирование?'"
                system_prompt += "\n\n⚠️ КРИТИЧНО: Используй ТОЛЬКО данные из таблицы выше, не придумывай!"
        
        # Формируем сообщения
        messages = [{"role": "system", "content": system_prompt}]
        
        # Добавляем историю (последние 10 сообщений)
        if history:
            for msg in history[-10:]:
                messages.append({"role": msg["role"], "content": msg["content"]})
        
        # Добавляем текущее сообщение
        messages.append({"role": "user", "content": message})
        
        # Генерируем ответ
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=500,
            temperature=0.7
        )
        
        text = response.choices[0].message.content
        
        # Убираем markdown форматирование для Telegram
        text = self._clean_markdown(text)
        
        return text
    
    def _clean_markdown(self, text: str) -> str:
        """Убираем markdown форматирование, которое не работает в Telegram."""
        import re
        
        # Убираем жирный текст **text** и __text__
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'__(.+?)__', r'\1', text)
        
        # Убираем курсив *text* и _text_ (осторожно, не ломаем смайлики)
        text = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'\1', text)
        
        # Убираем `code`
        text = re.sub(r'`([^`]+)`', r'\1', text)
        
        # Убираем [text](url) -> text: url
        text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1: \2', text)
        
        return text
    
    def extract_lead_data(self, message: str, current_data: dict = None) -> dict:
        """
        Извлечь данные лида из сообщения пользователя.
        
        Returns:
            Обновлённые данные лида
        """
        if current_data is None:
            current_data = {}
        
        # Добавляем контекст даты
        now = datetime.now()
        days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        date_context = f"СЕГОДНЯ: {now.strftime('%d.%m.%Y')} ({days[now.weekday()]})"
        
        prompt = f"""Извлеки информацию о бронировании праздника из сообщения.
{date_context}

Сообщение: "{message}"

Уже известные данные:
{current_data}

Извлеки ТОЛЬКО то, что ЯВНО указано в сообщении. Верни JSON:
{{
    "customer_name": "имя родителя/заказчика или null",
    "child_name": "имя ребёнка-именинника или null",
    "child_age": число (возраст) или null,
    "event_date": "дата праздника (число месяц) или null",
    "time": "время начала (например 10:30, 14:30, 18:30) или null",
    "kids_count": число детей или null,
    "adults_count": число взрослых или null,
    "phone": "номер телефона или null",
    "room": "название комнаты (Опушка, Поляна Чудес и т.д.) или null",
    "format": "Тематическая комната / Ресторан / null",
    "extras": ["список услуг, которые клиент хочет заказать"]
}}

EXTRAS — ДОПОЛНИТЕЛЬНЫЕ УСЛУГИ (добавляй ТОЛЬКО когда клиент явно заказывает/просит добавить):
- "аниматор" — если хочет аниматора, можно уточнить персонажа
- "торт" — если хочет заказать торт у нас
- "шары" — украшение шарами
- "фотограф" — если хочет фотографа
- "аквагрим" — если хочет аквагрим
- "меню" — если хочет предзаказ еды
- "мастер-класс" — если хочет мастер-класс
- "украшение комнаты" — доп. декор

ВАЖНО: 
- Не выдумывай данные! Если в сообщении нет информации — ставь null.
- Если написано "10.30" или "10:30" — это время, запиши в time.
- Если написано "11 февраля" или "2 марта" — это дата, запиши в event_date.
- Если написано "7 детей" или "детей 7" — запиши kids_count: 7.
- Если написано "опушка" — запиши room: "Опушка".
- ФОРМАТ:
  - "комната", "тематическая комната", "комнатка" → format: "Тематическая комната"
  - "ресторан", "в ресторане", "зал ресторана", "столик" → format: "Ресторан"
- EXTRAS: добавляй в массив только если клиент ЯВНО заказывает/подтверждает услугу.
- Если просто спрашивает/уточняет (напр. "можно свой торт?") — extras должны быть пустыми.
Ответь ТОЛЬКО JSON, без пояснений."""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0
            )
            
            import json
            result = response.choices[0].message.content.strip()
            # Убираем markdown если есть
            if result.startswith("```"):
                result = result.split("```")[1]
                if result.startswith("json"):
                    result = result[4:]
            
            extracted = json.loads(result)
            
            # Мержим с текущими данными (новые перезаписывают)
            for key, value in extracted.items():
                if value is not None and value != [] and value != "":
                    current_data[key] = value
            
            return current_data
            
        except Exception as e:
            print(f"Lead extraction error: {e}")
            return current_data


# Глобальный экземпляр агента
agent = Agent()
