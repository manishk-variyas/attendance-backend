from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.services.database.base_service import BaseService
from app.models.system_setting import SystemSetting
from datetime import datetime


class SystemSettingService(BaseService[SystemSetting]):
    def __init__(self, db_session: Session):
        super().__init__(db_session)

    def fetch(self) -> Optional[SystemSetting]:
        stmt = select(SystemSetting).where(SystemSetting.id == "company")
        return self.db.execute(stmt).scalars().first()

    def upsert(self, company_name: str = "", logo_content_type: str = None, created_by: str = None, updated_by: str = None) -> SystemSetting:
        existing = self.fetch()
        now = datetime.utcnow()
        data = {"company_name": company_name, "updated_at": now}
        if logo_content_type:
            data["logo_content_type"] = logo_content_type
        if created_by:
            data["created_by"] = created_by
        if updated_by:
            data["updated_by"] = updated_by
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
            self.db.commit()
            return existing
        return self.create(SystemSetting, id="company", **data)
