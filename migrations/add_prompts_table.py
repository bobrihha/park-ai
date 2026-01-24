"""
Миграция: Добавление таблицы prompts для хранения системных промптов.
Дата: 2026-01-14
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
from sqlalchemy import text
from db import SessionLocal

logger = logging.getLogger(__name__)


def upgrade():
    """Добавить таблицу prompts."""
    db = SessionLocal()
    
    try:
        # Проверяем существует ли таблица
        result = db.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='prompts'"
        ))
        if result.fetchone():
            logger.info("Table 'prompts' already exists")
            return
        
        # Создаём таблицу
        db.execute(text("""
            CREATE TABLE prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                park_id VARCHAR(10) DEFAULT 'nn',
                intent VARCHAR(30),
                name VARCHAR(100),
                content TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))
        
        # Создаём индексы
        db.execute(text("CREATE INDEX ix_prompts_park_id ON prompts (park_id)"))
        db.execute(text("CREATE INDEX ix_prompts_intent ON prompts (intent)"))
        
        db.commit()
        logger.info("Table 'prompts' created successfully")
        
    except Exception as e:
        db.rollback()
        logger.error(f"Migration failed: {e}")
        raise
    finally:
        db.close()


def downgrade():
    """Удалить таблицу prompts."""
    db = SessionLocal()
    try:
        db.execute(text("DROP TABLE IF EXISTS prompts"))
        db.commit()
        logger.info("Table 'prompts' dropped")
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    upgrade()
    print("Migration completed!")
