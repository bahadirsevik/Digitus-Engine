#!/usr/bin/env python
"""
Database initialization script.
Creates all tables and runs migrations.
"""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database.connection import engine, init_db
from app.database.models import Base


def main():
    """Initialize the database."""
    print("🔧 Initializing DIGITUS ENGINE database...")
    
    try:
        # Create all tables
        print("📦 Creating database tables...")
        init_db()
        
        print("✅ Database initialized successfully!")
        print("\nTables created:")
        for table_name in Base.metadata.tables.keys():
            print(f"  - {table_name}")
        
    except Exception as e:
        print(f"❌ Error initializing database: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
