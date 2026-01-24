"""
Migration: Add AmoCRM fields to leads table.
Run: python migrations/add_amocrm_fields.py
"""

import sys
sys.path.insert(0, '.')

from sqlalchemy import text
from db.database import engine


def migrate():
    """Add AmoCRM fields to leads table."""
    with engine.connect() as conn:
        # Check if columns already exist
        result = conn.execute(text(
            "PRAGMA table_info(leads)"
        ))
        columns = [row[1] for row in result.fetchall()]
        
        if 'amocrm_deal_id' not in columns:
            conn.execute(text(
                "ALTER TABLE leads ADD COLUMN amocrm_deal_id VARCHAR(50)"
            ))
            print("✅ Added amocrm_deal_id column")
        else:
            print("⏭️  amocrm_deal_id already exists")
        
        if 'amocrm_contact_id' not in columns:
            conn.execute(text(
                "ALTER TABLE leads ADD COLUMN amocrm_contact_id VARCHAR(50)"
            ))
            print("✅ Added amocrm_contact_id column")
        else:
            print("⏭️  amocrm_contact_id already exists")
        
        # Create index
        try:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_leads_amocrm_deal_id ON leads(amocrm_deal_id)"
            ))
            print("✅ Created index on amocrm_deal_id")
        except Exception as e:
            print(f"⏭️  Index already exists or error: {e}")
        
        conn.commit()
        print("\n🎉 Migration complete!")


if __name__ == "__main__":
    migrate()
