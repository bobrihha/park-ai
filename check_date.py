from datetime import datetime
now = datetime.now()
days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
current_date_str = now.strftime("%d.%m.%Y")
current_day = days[now.weekday()]
print(f"Server time: {now}")
print(f"Calculated: {current_date_str} ({current_day})")
print(f"Weekday index: {now.weekday()}")
