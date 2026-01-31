"""
Migration: Add birthday_state, vk_id, web_session_id to leads table.

Run with: python3 -m migrations.add_birthday_state
"""

from db.database import SessionLocal
from sqlalchemy import text, inspect


def column_exists(db, table_name: str, column_name: str) -> bool:
    """Check if column exists in table."""
    inspector = inspect(db.get_bind())
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def run_migration():
    """Add new columns to leads table."""
    db = SessionLocal()
    
    try:
        # Add birthday_state column
        if not column_exists(db, 'leads', 'birthday_state'):
            db.execute(text("ALTER TABLE leads ADD COLUMN birthday_state VARCHAR(30) DEFAULT 'idle'"))
            print("✅ Added birthday_state column")
        else:
            print("ℹ️  birthday_state column already exists")
        
        # Add vk_id column
        if not column_exists(db, 'leads', 'vk_id'):
            db.execute(text("ALTER TABLE leads ADD COLUMN vk_id VARCHAR(50)"))
            print("✅ Added vk_id column")
        else:
            print("ℹ️  vk_id column already exists")
        
        # Add web_session_id column
        if not column_exists(db, 'leads', 'web_session_id'):
            db.execute(text("ALTER TABLE leads ADD COLUMN web_session_id VARCHAR(100)"))
            print("✅ Added web_session_id column")
        else:
            print("ℹ️  web_session_id column already exists")
        
        db.commit()
        print("\n✅ Migration completed successfully!")
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run_migration()
