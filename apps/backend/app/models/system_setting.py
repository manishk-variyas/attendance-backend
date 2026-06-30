from datetime import datetime, time
from sqlalchemy import Column, String, Time, Integer, DateTime, text
from app.core.models import Base


class SystemSetting(Base):
    __tablename__ = "system_settings"

    id = Column(String(50), primary_key=True)
    company_name = Column(String(255), nullable=False, server_default=text("''"))
    logo_content_type = Column(String(100), nullable=True)
    created_by = Column(String(255), nullable=True)
    updated_by = Column(String(255), nullable=True)
    default_shift_start_time = Column(Time, nullable=True)
    default_shift_end_time = Column(Time, nullable=True)
    default_timezone = Column(String(50), nullable=True, server_default=text("'Asia/Kolkata'"))
    grace_minutes = Column(Integer, nullable=True, server_default=text("15"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "company_name": self.company_name,
            "logo_url": "",
            "default_shift_start_time": self.default_shift_start_time.isoformat() if self.default_shift_start_time else None,
            "default_shift_end_time": self.default_shift_end_time.isoformat() if self.default_shift_end_time else None,
            "default_timezone": self.default_timezone,
            "grace_minutes": self.grace_minutes,
            "updated_at": self.updated_at,
        }
