import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, DateTime, text
from sqlalchemy.dialects.postgresql import UUID
from geoalchemy2 import Geometry
from app.core.models import Base


class UserLocation(Base):
    __tablename__ = "user_locations"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    email = Column(String(255), nullable=False, unique=True, index=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    location_name = Column(String(500), nullable=True)
    geom = Column(Geometry(geometry_type="POINT", srid=4326), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=text("now()"))

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "email": self.email,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "updated_at": self.updated_at,
        }
