from datetime import datetime
from sqlalchemy import Column, String, DateTime, text
from app.core.models import Base


class SystemSetting(Base):
    __tablename__ = "system_settings"

    id = Column(String(50), primary_key=True)
    company_name = Column(String(255), nullable=False, server_default=text("''"))
    logo_content_type = Column(String(100), nullable=True)
    created_by = Column(String(255), nullable=True)
    updated_by = Column(String(255), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "company_name": self.company_name,
            "logo_url": "",
            "updated_at": self.updated_at,
        }
