import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Date, Time, DateTime, text
from sqlalchemy.dialects.postgresql import UUID
from app.core.models import Base


class Shift(Base):
    __tablename__ = "shifts"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    keycloak_user_id = Column(String(255), nullable=False, index=True)
    redmine_user_id = Column(Integer, nullable=True)
    user_email = Column(String(255), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    end_date = Column(Date, nullable=True)
    shift_code = Column(String(50), nullable=False)
    project_id = Column(Integer, nullable=True)
    project_name = Column(String(255), nullable=True)
    work_location_status = Column(String(6), nullable=False)
    work_address = Column(String(500), server_default=text("'N/A'"))
    shift_start_time = Column(Time, nullable=True)
    shift_end_time = Column(Time, nullable=True)
    shift_start_utc = Column(DateTime(timezone=True), nullable=True)
    shift_end_utc = Column(DateTime(timezone=True), nullable=True)
    country = Column(String(10), server_default=text("''"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "keycloakUserId": self.keycloak_user_id,
            "redmineUserId": self.redmine_user_id,
            "userEmail": self.user_email,
            "date": self.date.isoformat() if self.date else None,
            "endDate": self.end_date.isoformat() if self.end_date else None,
            "shiftCode": self.shift_code,
            "projectId": self.project_id,
            "projectName": self.project_name,
            "workLocationStatus": self.work_location_status,
            "workAddress": self.work_address,
            "shiftStartTime": self.shift_start_time.isoformat() if self.shift_start_time else None,
            "shiftEndTime": self.shift_end_time.isoformat() if self.shift_end_time else None,
            "shiftStartUTC": self.shift_start_utc,
            "shiftEndUTC": self.shift_end_utc,
            "country": self.country,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }
