import uuid
from datetime import datetime
from sqlalchemy import Column, String, Time, DateTime, Integer, Boolean, text
from sqlalchemy.dialects.postgresql import UUID
from app.core.models import Base
from app.constants import (
    DEFAULT_AUTO_CHECKOUT_ENABLED,
    DEFAULT_AUTO_CHECKOUT_GRACE_MINUTES,
    DEFAULT_GRACE_MINUTES,
    DEFAULT_CHECKIN_REMINDER_DELAY_MINUTES,
    DEFAULT_CHECKOUT_REMINDER_GRACE_HOURS,
    DEFAULT_EARLY_CHECKIN_LIMIT_MINUTES,
    DEFAULT_CHECKIN_CUTOFF_MINUTES,
    DEFAULT_HALF_DAY_MINUTES,
)


class ShiftDefinition(Base):
    __tablename__ = "shift_master"

    shift_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    shift_code = Column(String(50), nullable=False, unique=True, index=True)
    shift_name = Column(String(255), nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    timezone = Column(String(50), nullable=False, server_default=text("'Asia/Kolkata'"))
    country = Column(String(100), server_default=text("''"))

    # ── Timing policy (per shift, single source of truth) ────────────────────
    # Auto-checkout fires at shift_end + auto_checkout_grace_minutes.
    auto_checkout_enabled = Column(Boolean, nullable=False, server_default=text(str(DEFAULT_AUTO_CHECKOUT_ENABLED).lower()))
    auto_checkout_grace_minutes = Column(Integer, nullable=False, server_default=text(str(DEFAULT_AUTO_CHECKOUT_GRACE_MINUTES)))
    # Late/early check-in and check-out tolerance.
    grace_minutes = Column(Integer, nullable=False, server_default=text(str(DEFAULT_GRACE_MINUTES)))
    # How long after shift start before a missed check-in reminder is sent.
    checkin_reminder_delay_minutes = Column(Integer, nullable=False, server_default=text(str(DEFAULT_CHECKIN_REMINDER_DELAY_MINUTES)))
    # How long after shift end before a check-out reminder is sent.
    checkout_reminder_grace_hours = Column(Integer, nullable=False, server_default=text(str(DEFAULT_CHECKOUT_REMINDER_GRACE_HOURS)))
    # Earliest a check-in is accepted (before shift start).
    early_checkin_limit_minutes = Column(Integer, nullable=False, server_default=text(str(DEFAULT_EARLY_CHECKIN_LIMIT_MINUTES)))
    # Latest a check-in is accepted (after shift start).
    checkin_cutoff_minutes = Column(Integer, nullable=False, server_default=text(str(DEFAULT_CHECKIN_CUTOFF_MINUTES)))
    # Below this many worked minutes the day is flagged half_day.
    half_day_minutes = Column(Integer, nullable=False, server_default=text(str(DEFAULT_HALF_DAY_MINUTES)))

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
            "autoCheckoutEnabled": self.auto_checkout_enabled,
            "autoCheckoutGraceMinutes": self.auto_checkout_grace_minutes,
            "graceMinutes": self.grace_minutes,
            "checkinReminderDelayMinutes": self.checkin_reminder_delay_minutes,
            "checkoutReminderGraceHours": self.checkout_reminder_grace_hours,
            "earlyCheckinLimitMinutes": self.early_checkin_limit_minutes,
            "checkinCutoffMinutes": self.checkin_cutoff_minutes,
            "halfDayMinutes": self.half_day_minutes,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }
