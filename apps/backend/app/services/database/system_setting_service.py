from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.services.database.base_service import BaseService
from app.models.system_setting import SystemSetting
from datetime import datetime, date


class SystemSettingService(BaseService[SystemSetting]):
    def __init__(self, db_session: Session):
        super().__init__(db_session)

    def fetch(self) -> Optional[SystemSetting]:
        stmt = select(SystemSetting).where(SystemSetting.id == "company")
        return self.db.execute(stmt).scalars().first()

    def upsert(self, company_name: str = "", logo_content_type: str = None, created_by: str = None, updated_by: str = None,
               default_shift_start_time: str = None, default_shift_end_time: str = None,
               default_timezone: str = None, grace_minutes: int = None,
               checkout_reminder_grace_hours: int = None, auto_checkout_enabled: bool = None,
               incorporation_date: str = None) -> SystemSetting:
        existing = self.fetch()
        now = datetime.utcnow()
        data = {"company_name": company_name, "updated_at": now}
        if logo_content_type:
            data["logo_content_type"] = logo_content_type
        if created_by:
            data["created_by"] = created_by
        if updated_by:
            data["updated_by"] = updated_by
        if default_shift_start_time is not None:
            data["default_shift_start_time"] = datetime.strptime(default_shift_start_time, "%H:%M").time() if default_shift_start_time else None
        if default_shift_end_time is not None:
            data["default_shift_end_time"] = datetime.strptime(default_shift_end_time, "%H:%M").time() if default_shift_end_time else None
        if default_timezone is not None:
            data["default_timezone"] = default_timezone
        if grace_minutes is not None:
            data["grace_minutes"] = grace_minutes
        if checkout_reminder_grace_hours is not None:
            data["checkout_reminder_grace_hours"] = checkout_reminder_grace_hours
        if auto_checkout_enabled is not None:
            data["auto_checkout_enabled"] = auto_checkout_enabled
        if incorporation_date is not None:
            data["incorporation_date"] = date.fromisoformat(incorporation_date) if incorporation_date else None
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
            self.db.commit()
            return existing
        return self.create(SystemSetting, id="company", **data)
