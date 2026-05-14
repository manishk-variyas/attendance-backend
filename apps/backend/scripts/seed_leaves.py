import asyncio
import sys
import os
from datetime import datetime, timedelta

# Add the app directory to sys.path to allow imports
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app.core.mongodb import connect_to_mongo, close_mongo_connection, get_mongodb
from app.core.config import settings

async def seed_data():
    await connect_to_mongo()
    db = get_mongodb()

    print("Seeding Holidays...")
    holidays_collection = db["holidays"]
    await holidays_collection.delete_many({}) # Clear existing
    
    current_year = datetime.now().year
    holidays = [
        {"date": datetime(current_year, 1, 1), "name": "New Year's Day", "description": "Public Holiday"},
        {"date": datetime(current_year, 1, 26), "name": "Republic Day", "description": "National Holiday"},
        {"date": datetime(current_year, 8, 15), "name": "Independence Day", "description": "National Holiday"},
        {"date": datetime(current_year, 10, 2), "name": "Gandhi Jayanti", "description": "National Holiday"},
        {"date": datetime(current_year, 12, 25), "name": "Christmas", "description": "Public Holiday"},
    ]
    await holidays_collection.insert_many(holidays)
    print(f"Inserted {len(holidays)} holidays.")

    print("Seeding example Leave Balances...")
    # Note: In a real app, you'd seed this for all users or on user creation
    # For now, we'll leave it empty or add a placeholder for a specific user ID if known
    # but the API handles the missing doc gracefully.
    
    await close_mongo_connection()

if __name__ == "__main__":
    asyncio.run(seed_data())
