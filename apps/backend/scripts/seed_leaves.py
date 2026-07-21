import asyncio
import sys
import os
from datetime import datetime, timedelta

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app.core.database import SessionLocal, init_db
from app.models.holiday import Holiday
from app.services.database.holiday_service import HolidayService


async def seed_data():
    print("Initializing tables...")
    init_db()

    db = SessionLocal()
    try:
        svc = HolidayService(db)
        print("Seeding Holidays...")
        for h in svc.fetch_all(Holiday):
            svc.delete(Holiday, h.id)

        current_year = datetime.now().year
        holidays = [
            {"country_code": "IN", "holiday_date": datetime(current_year, 1, 1).date(), "holiday_name": "New Year's Day", "holiday_type": "GAZETTED", "is_national": True},
            {"country_code": "IN", "holiday_date": datetime(current_year, 1, 26).date(), "holiday_name": "Republic Day", "holiday_type": "GAZETTED", "is_national": True},
            {"country_code": "IN", "holiday_date": datetime(current_year, 8, 15).date(), "holiday_name": "Independence Day", "holiday_type": "GAZETTED", "is_national": True},
            {"country_code": "IN", "holiday_date": datetime(current_year, 10, 2).date(), "holiday_name": "Gandhi Jayanti", "holiday_type": "GAZETTED", "is_national": True},
            {"country_code": "IN", "holiday_date": datetime(current_year, 12, 25).date(), "holiday_name": "Christmas", "holiday_type": "GAZETTED", "is_national": False},
        ]
        for h in holidays:
            svc.create(Holiday, **h)
        print(f"Inserted {len(holidays)} holidays.")
        print("Done!")
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(seed_data())
