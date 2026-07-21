import uuid
from datetime import datetime
from sqlalchemy import Column, String, Time, DateTime, text
from sqlalchemy.dialects.postgresql import UUID
from app.core.models import Base


class ShiftDefinition(Base):
    __tablename__ = "shift_master"

    shift_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    shift_code = Column(String(50), nullable=False, unique=True, index=True)
    shift_name = Column(String(255), nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    timezone = Column(String(50), nullable=False, server_default=text("'Asia/Kolkata'"))
    country = Column(String(100), server_default=text("''"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    def to_dict(self) -> dict:
        return {
            "id": str(self.shift_id),
            "shiftCode": self.shift_code,
            "shiftName": self.shift_name,
            "startTime": self.start_time.isoformat() if self.start_time else None,
            "endTime": self.end_time.isoformat() if self.end_time else None,
            "timezone": self.timezone,
            "country": self.country,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }
