
import sys
import os

# Добавляем путь к корню проекта
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agent import Agent
from datetime import datetime

# Мок для конфигурации, если .env не загрузится (но он должен)
from dotenv import load_dotenv
load_dotenv()

def test_date_intelligence():
    print("=== TEST 1: DATE AWARENESS ===")
    agent = Agent()
    
    # 29 января 2026 - это Четверг (будни, 1190р)
    # Спрашиваем цену на эту дату
    response = agent.generate_response(
        message="Какая цена билета 29 января?",
        intent="general"
    )
    
    print(f"BOT RESPONSE:\n{response}\n")
    
    if "1190" in response or "будн" in response.lower():
        print("✅ PASS: Бот определил будний день/цену")
    else:
        print("❌ FAIL: Бот не определил цену")

def test_event_intelligence():
    print("\n=== TEST 2: EVENT AWARENESS ===")
    agent = Agent()
    
    # Спрашиваем про Музыкальное лото (29 января)
    response = agent.generate_response(
        message="Что будет 29 января?",
        intent="events"
    )
    
    print(f"BOT RESPONSE:\n{response}\n")
    
    if "лото" in response.lower():
        print("✅ PASS: Бот нашел музыкальное лото")
    else:
        print("❌ FAIL: Бот не нашел мероприятие")

if __name__ == "__main__":
    try:
        test_date_intelligence()
        test_event_intelligence()
    except Exception as e:
        print(f"ERROR: {e}")
