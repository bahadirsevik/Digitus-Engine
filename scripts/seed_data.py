#!/usr/bin/env python
"""
Seed data script.
Populates the database with test keywords.
"""
import sys
import os
import random

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database.connection import SessionLocal
from app.database.crud import create_keyword, get_keyword_by_text


# Örnek anahtar kelimeler
SAMPLE_KEYWORDS = [
    {"keyword": "laptop satın al", "sector": "teknoloji", "target_market": "TR"},
    {"keyword": "en iyi telefon 2024", "sector": "teknoloji", "target_market": "TR"},
    {"keyword": "python programlama", "sector": "eğitim", "target_market": "TR"},
    {"keyword": "istanbul restoran", "sector": "yeme-içme", "target_market": "TR"},
    {"keyword": "online kurs", "sector": "eğitim", "target_market": "TR"},
    {"keyword": "dijital pazarlama", "sector": "pazarlama", "target_market": "TR"},
    {"keyword": "seo nedir", "sector": "pazarlama", "target_market": "TR"},
    {"keyword": "e-ticaret sitesi", "sector": "teknoloji", "target_market": "TR"},
    {"keyword": "instagram takipçi", "sector": "sosyal medya", "target_market": "TR"},
    {"keyword": "youtube para kazanma", "sector": "sosyal medya", "target_market": "TR"},
    {"keyword": "kripto para yatırımı", "sector": "finans", "target_market": "TR"},
    {"keyword": "ev dekorasyonu", "sector": "ev-yaşam", "target_market": "TR"},
    {"keyword": "fitness programı", "sector": "sağlık", "target_market": "TR"},
    {"keyword": "diyet listesi", "sector": "sağlık", "target_market": "TR"},
    {"keyword": "uzaktan çalışma", "sector": "iş", "target_market": "TR"},
    {"keyword": "freelance iş", "sector": "iş", "target_market": "TR"},
    {"keyword": "marka oluşturma", "sector": "pazarlama", "target_market": "TR"},
    {"keyword": "içerik pazarlama", "sector": "pazarlama", "target_market": "TR"},
    {"keyword": "google ads kampanya", "sector": "pazarlama", "target_market": "TR"},
    {"keyword": "sosyal medya yönetimi", "sector": "pazarlama", "target_market": "TR"},
]


def generate_metrics():
    """Rastgele metrikler oluşturur."""
    return {
        "monthly_volume": random.randint(100, 50000),
        "trend_12m": round(random.uniform(-20, 100), 2),
        "trend_3m": round(random.uniform(-10, 150), 2),
        "competition_score": round(random.uniform(0.1, 0.95), 2),
    }


def main():
    """Seed data oluşturur."""
    print("🌱 Seeding DIGITUS ENGINE database...")
    
    db = SessionLocal()
    created = 0
    skipped = 0
    
    try:
        for kw_base in SAMPLE_KEYWORDS:
            # Check if exists
            if get_keyword_by_text(db, kw_base["keyword"]):
                skipped += 1
                continue
            
            # Add metrics
            kw_data = {**kw_base, **generate_metrics()}
            
            create_keyword(db, kw_data)
            created += 1
            print(f"  ✓ {kw_base['keyword']}")
        
        print(f"\n✅ Seed completed: {created} created, {skipped} skipped")
    
    except Exception as e:
        print(f"❌ Error seeding data: {e}")
        db.rollback()
        sys.exit(1)
    
    finally:
        db.close()


if __name__ == "__main__":
    main()
