from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.services.database.base_service import BaseService
from app.models.location import UserLocation
from datetime import datetime, timezone


class LocationService(BaseService[UserLocation]):
    def __init__(self, db_session: Session):
        super().__init__(db_session)

    def fetch_by_email(self, email: str) -> Optional[UserLocation]:
        stmt = select(UserLocation).where(UserLocation.email == email)
        return self.db.execute(stmt).scalars().first()

    def upsert(self, email: str, latitude: float, longitude: float) -> UserLocation:
        existing = self.fetch_by_email(email)
        if existing:
            existing.latitude = latitude
            existing.longitude = longitude
            existing.updated_at = datetime.now(timezone.utc)
            self.db.commit()
            return existing
        return self.create(UserLocation, email=email, latitude=latitude, longitude=longitude)
