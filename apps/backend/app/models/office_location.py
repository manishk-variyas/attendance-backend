import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, DateTime, text
from sqlalchemy.dialects.postgresql import UUID
from geoalchemy2 import Geometry
from app.core.models import Base


class OfficeLocation(Base):
    """
    Stores admin-defined office/work-site locations used for geofencing.

    Each row represents a physical office or customer site.
    Employees are assigned to a location via employee_master.location_id.

    During shift check-in, the employee's GPS (user_locations table) is
    compared against the assigned office's lat/lng + radius_meters to
    validate that workStatus=OFFICE is legitimate.
    """
    __tablename__ = "office_locations"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    category = Column(String(50), nullable=False, server_default=text("'Office'"))
    name = Column(String(255), nullable=False)           # "Noida Office", "Delhi HQ"
    address = Column(String(500), nullable=True)
    city = Column(String(100), nullable=True)
    country = Column(String(10), nullable=True, server_default=text("''"))
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    radius_meters = Column(Float, nullable=False, server_default=text("300"))  # geofence radius
    created_by = Column(String(255), nullable=True)      # admin email who created it
    geom = Column(Geometry("POINT", srid=4326), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "category": self.category,
            "name": self.name,
            "address": self.address,
            "city": self.city,
            "country": self.country,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "radius_meters": self.radius_meters,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
