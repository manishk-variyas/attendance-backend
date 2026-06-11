import uuid
from sqlalchemy import Column, String, Date, Boolean, text
from sqlalchemy.dialects.postgresql import UUID
from app.core.models import Base


class Holiday(Base):
    __tablename__ = "holidays"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    country_code = Column(String(10), nullable=False)
    region = Column(String(100), nullable=True)
    holiday_date = Column(Date, nullable=False)
    holiday_name = Column(String(255), nullable=False)
    holiday_type = Column(String(50), nullable=False)
    is_national = Column(Boolean, nullable=False, server_default=text("false"))

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "country_code": self.country_code,
            "region": self.region,
            "holiday_date": self.holiday_date,
            "holiday_name": self.holiday_name,
            "holiday_type": self.holiday_type,
            "is_national": self.is_national,
        }
