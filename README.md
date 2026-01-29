# Park AI Bot 🦁

Умный чат-бот для парка развлечений «Джунгли Сити» с поддержкой Telegram, VK и Web-чата.

## Возможности

- 🎂 **Бронирование дней рождения** — полный флоу с выбором формата, даты, времени
- 💬 **Ответы на вопросы** — RAG-система с базой знаний о парке
- 📞 **Интеграция с AmoCRM** — автоматическое создание сделок и контактов
- 🔍 **Потерянные вещи** — сбор данных и уведомление менеджерам
- 😔 **Обработка жалоб** — эскалация на менеджеров
- 📢 **Уведомления** — Telegram-канал для менеджеров

## Технологии

- **Python 3.11+**
- **FastAPI** — Web API
- **python-telegram-bot** — Telegram бот
- **vk_api** — VK бот  
- **OpenAI GPT-4o** — LLM для ответов
- **SQLAlchemy** — ORM для базы данных
- **AmoCRM API** — интеграция с CRM

## Структура проекта

```
├── api/                # FastAPI веб-чат
├── bot/                # Telegram бот handlers
├── vk_bot/             # VK бот handlers
├── core/               # Основная логика
│   ├── agent.py        # LLM агент
│   ├── rag.py          # RAG система
│   ├── amocrm.py       # AmoCRM интеграция
│   ├── flows/          # Бизнес-флоу (birthday, lost_item)
│   └── utils.py        # Утилиты
├── config/             # Конфигурация
├── data/               # Данные и конфиги
├── db/                 # Модели БД
├── knowledge/          # База знаний для RAG
├── static/             # Статические файлы (веб-виджет)
└── migrations/         # Миграции Alembic
```

## Установка

1. Клонировать репозиторий:
```bash
git clone https://github.com/bobrihha/park-ai.git
cd park-ai
```

2. Установить зависимости:
```bash
pip install -r requirements.txt
```

3. Создать `.env` файл по образцу:
```bash
cp .env.example .env
```

4. Заполнить переменные в `.env`:
```env
TELEGRAM_TOKEN=your_token
OPENAI_API_KEY=your_key
AMOCRM_TOKEN=your_token
# ... и другие
```

## Запуск

### Telegram бот
```bash
python run_tg_bot.py
```

### VK бот
```bash
python run_vk_bot.py
```

### Web API
```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

## Деплой (systemd)

Пример сервиса для Telegram бота:
```ini
[Unit]
Description=Jungle Bot Telegram
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/jungle_bot
ExecStart=/usr/bin/python3 run_tg_bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```

## Лицензия

Проприетарная лицензия. Все права защищены.

## Автор

Разработано с 💚 для «Джунгли Сити»
