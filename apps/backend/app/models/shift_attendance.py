import uuid
from datetime import date
from sqlalchemy import Column, String, Date, DateTime, Numeric, Boolean, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID
from app.core.models import Base


class ShiftAttendance(Base):
    __tablename__ = "shift_attendance"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    shift_id = Column(UUID(as_uuid=True), ForeignKey("shifts.id"), nullable=False, unique=True)
    keycloak_user_id = Column(String(255), nullable=False, index=True)
    attendance_date = Column(Date, nullable=False, index=True)
    shift_code = Column(String(50), nullable=True)
    check_in_time = Column(DateTime(timezone=True), nullable=True)
    check_in_lat = Column(Numeric(10, 8), nullable=True)
    check_in_lng = Column(Numeric(11, 8), nullable=True)
    check_in_location_name = Column(String(500), nullable=True)
    check_out_time = Column(DateTime(timezone=True), nullable=True)
    check_out_lat = Column(Numeric(10, 8), nullable=True)
    check_out_lng = Column(Numeric(11, 8), nullable=True)
    check_out_location_name = Column(String(500), nullable=True)
    work_location_status = Column(String(50), nullable=True)
    is_late = Column(Boolean, nullable=False, server_default=text("false"))
    status = Column(String(50), nullable=False, server_default=text("'in_progress'"))
    total_hours = Column(Numeric(5, 2), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "shift_id": str(self.shift_id) if self.shift_id else None,
            "keycloak_user_id": self.keycloak_user_id,
            "attendance_date": self.attendance_date.isoformat() if self.attendance_date else None,
            "shift_code": self.shift_code,
            "check_in_time": self.check_in_time.isoformat() if self.check_in_time else None,
            "check_in_lat": float(self.check_in_lat) if self.check_in_lat else None,
            "check_in_lng": float(self.check_in_lng) if self.check_in_lng else None,
            "check_in_location_name": self.check_in_location_name,
            "check_out_time": self.check_out_time.isoformat() if self.check_out_time else None,
            "check_out_lat": float(self.check_out_lat) if self.check_out_lat else None,
            "check_out_lng": float(self.check_out_lng) if self.check_out_lng else None,
            "check_out_location_name": self.check_out_location_name,
            "work_location_status": self.work_location_status,
            "is_late": self.is_late,
            "status": self.status,
            "total_hours": float(self.total_hours) if self.total_hours else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
