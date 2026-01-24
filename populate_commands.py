from db import SessionLocal, BotCommand, init_db

def populate():
    init_db()
    db = SessionLocal()
    
    # Default commands content (simplified from handlers.py)
    commands = [
        {
            "command": "prices",
            "title": "💰 Цены",
            "response": """💰 <b>Цены на безлимитный билет</b>

🟢 Понедельник (супер-цена): <b>990 ₽</b>
🔵 Будни (вт-пт): <b>1190 ₽</b>
🔴 Выходные: <b>1590 ₽</b>

✅ Взрослые — БЕСПЛАТНО
✅ Дети до 1 года — БЕСПЛАТНО

<b>Скидки:</b>
• Дети 1-4 года: -20% в будни
• Многодетные: -30% (вт-вс)
• После 20:00: -50%
• Именинник: -50% (±5 дней от ДР)

Напишите, если нужна помощь с расчётом! 😊""",
             "order": 1
        },
        {
            "command": "schedule",
            "title": "🕐 Режим работы",
            "response": """🕐 <b>Режим работы Джунгли Сити</b>

📍 Нижний Новгород, ТЦ «Лента»

• Понедельник: 12:00 - 22:00
• Вторник - Воскресенье: 10:00 - 22:00

⚠️ Вход в парк до 21:00
🍕 Ресторан принимает заказы до 21:00""",
             "order": 2
        },
        {
            "command": "afisha",
            "title": "🎪 Афиша",
            "response": """🎪 <b>Афиша Джунгли Сити</b>

Актуальные события и мероприятия:
👉 <a href='https://nn.jucity.ru/afisha/'>Открыть афишу</a>

У нас регулярно проходят:
• Шоу-программы
• Мастер-классы
• Дискотеки
• Праздничные мероприятия

Спрашивайте — расскажу подробнее! 🌟""",
             "order": 3
        },
        {
            "command": "rules",
            "title": "📋 Правила",
            "response": """📋 <b>Правила посещения Джунгли Сити</b>

🧦 <b>Носки обязательны</b> на игровой территории

👨‍👩‍👧 <b>Дети под присмотром</b> взрослых

🍕 <b>Своя еда запрещена</b>
   (кроме детского питания и воды)

🚫 <b>Запрещено:</b>
• Алкоголь
• Домашние животные
• Опасные предметы

♿ Есть пандусы и лифты через ТЦ «Лента»""",
             "order": 4
        },
         {
            "command": "contacts",
            "title": "📍 Контакты",
            "response": """📍 <b>Как нас найти</b>

<b>Адрес:</b>
г. Нижний Новгород, ул. Коминтерна, д. 11
ТЦ «Лента», 1 этаж

<b>Телефоны:</b>
📞 +7 (831) 213-50-50
💬 WhatsApp: +7 (962) 509-74-93

<b>Как добраться:</b>
🚇 Метро «Буревестник» — 250 м
🚌 Автобус 90, 95, 71, 78, 29 → ост. «Варя»
🚋 Троллейбус 5, 8 → ост. «Варя»
🚗 Бесплатная парковка у ТЦ""",
             "order": 6
        },
        {
            "command": "cafe",
            "title": "🍕 Меню кафе",
            "response": """🍕 <b>Ресторан Джунгли Сити</b>

У нас вкусно и для детей, и для взрослых!

📖 <b>Меню:</b>
👉 <a href='https://catalog.botcicada.ru/menu.html'>Открыть меню</a>

🎂 <b>Торты на заказ:</b>
👉 <a href='https://catalog.botcicada.ru/cakes.html'>Каталог тортов</a>

⏰ Ресторан работает до 21:00""",
             "order": 7
        },
        {
            "command": "promo",
            "title": "🎁 Акции",
            "response": """🎁 <b>Скидки и акции Джунгли Сити</b>

💚 <b>Постоянные скидки:</b>
• Дети 1-4 года: -20% в будни
• Многодетные семьи: -30% (вт-вс)
• После 20:00: -50%
• Именинник: -50% (±5 дней от ДР)
• Дети с инвалидностью: бесплатно в будни

🎂 <b>На праздниках:</b>
• От 7 детей — именинник БЕСПЛАТНО
• Взрослые всегда бесплатно
• Комната на 3 часа — бесплатно

📍 Актуальные акции на сайте:
👉 <a href='https://nn.jucity.ru/'>nn.jucity.ru</a>""",
             "order": 8
        },
        {
            "command": "start",
            "title": "🏠 Главное меню",
            "response": "Start command logic is handled by code.",
            "order": 0
        },
        {
            "command": "birthday",
            "title": "🎂 День рождения",
            "response": "Birthday logic is handled by code.",
            "order": 5
        },
        {
            "command": "human",
            "title": "👤 Менеджер",
            "response": "Human escalation logic.",
            "order": 9
        },
    ]

    for data in commands:
        exists = db.query(BotCommand).filter(BotCommand.command == data["command"]).first()
        if not exists:
            cmd = BotCommand(
                command=data["command"],
                title=data["title"],
                response=data["response"],
                order=data["order"],
                is_active=True,
                has_logic=True # Это специальные команды
            )
            db.add(cmd)
            print(f"Added command /{data['command']}")
        else:
             # Если команда есть, но мы хотим обновить has_logic или title (опционально)
             pass
    
    db.commit()
    db.close()
    print("Done!")

if __name__ == "__main__":
    populate()
