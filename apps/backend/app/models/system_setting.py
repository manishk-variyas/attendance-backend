from datetime import datetime, time
from sqlalchemy import Column, String, Time, Integer, Boolean, DateTime, Date, text
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
    checkout_reminder_grace_hours = Column(Integer, nullable=True, server_default=text("2"))
    auto_checkout_enabled = Column(Boolean, nullable=False, server_default=text("true"))
    auto_checkout_cutoff_time = Column(Time, nullable=True, server_default=text("'22:00'"))
    incorporation_date = Column(Date, nullable=True, server_default=text("'2016-06-09'"))
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
            "checkout_reminder_grace_hours": self.checkout_reminder_grace_hours,
            "auto_checkout_enabled": self.auto_checkout_enabled,
            "auto_checkout_cutoff_time": self.auto_checkout_cutoff_time.isoformat() if self.auto_checkout_cutoff_time else None,
            "incorporation_date": self.incorporation_date.isoformat() if self.incorporation_date else None,
            "updated_at": self.updated_at,
        }
