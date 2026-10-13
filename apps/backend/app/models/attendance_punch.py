import uuid
from sqlalchemy import (
    Column, String, Date, DateTime, Numeric, Boolean, ForeignKey, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.core.models import Base


class AttendancePunch(Base):
    """One check-in/out punch per shift (the multi-shift source of truth).

    `attendance` stays the daily rollup; this table holds the per-shift reality.
    `attendance_date` is the shift's local date (see docs/multi-shift-punch-plan.md).
    """

    __tablename__ = "attendance_punches"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    shift_id = Column(
        UUID(as_uuid=True),
        ForeignKey("shifts.id", ondelete="SET NULL"),
        nullable=True, unique=True,
    )
    keycloak_user_id = Column(String(255), nullable=False, index=True)
    attendance_date = Column(Date, nullable=False, index=True)
    shift_code = Column(String(50), ForeignKey("shift_master.shift_code"), nullable=True)

    check_in_time = Column(DateTime(timezone=True), nullable=True)
    check_in_lat = Column(Numeric(10, 8), nullable=True)
    check_in_lng = Column(Numeric(11, 8), nullable=True)
    check_in_location_name = Column(String(500), nullable=True)
    check_in_location_id = Column(UUID(as_uuid=True), ForeignKey("office_locations.id", ondelete="SET NULL"), nullable=True)

    check_out_time = Column(DateTime(timezone=True), nullable=True)
    check_out_lat = Column(Numeric(10, 8), nullable=True)
    check_out_lng = Column(Numeric(11, 8), nullable=True)
    check_out_location_name = Column(String(500), nullable=True)
    check_out_location_id = Column(UUID(as_uuid=True), ForeignKey("office_locations.id", ondelete="SET NULL"), nullable=True)
    check_in_device = Column(String(100), nullable=True)
    check_out_device = Column(String(100), nullable=True)

    work_location_status = Column(String(20), nullable=True)
    is_late = Column(Boolean, nullable=False, server_default=text("false"))
    total_hours = Column(Numeric(5, 2), nullable=True)
    status = Column(String(20), nullable=False, server_default=text("'in_progress'"))

    is_manual_entry = Column(Boolean, nullable=False, server_default=text("false"))
    modified_by = Column(String(255), nullable=True)
    remarks = Column(String(500), nullable=True)
    is_synced = Column(Boolean, nullable=False, server_default=text("false"))
    server_received_at = Column(DateTime(timezone=True), nullable=True)
    is_deleted = Column(Boolean, nullable=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    shift = relationship("Shift", back_populates="attendance_punch")

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "shiftId": str(self.shift_id) if self.shift_id else None,
            "keycloakUserId": self.keycloak_user_id,
            "attendanceDate": self.attendance_date.isoformat() if self.attendance_date else None,
            "shiftCode": self.shift_code,
            "checkInTime": self.check_in_time.isoformat() if self.check_in_time else None,
            "checkInLocationId": str(self.check_in_location_id) if self.check_in_location_id else None,
            "checkOutTime": self.check_out_time.isoformat() if self.check_out_time else None,
            "checkOutLocationId": str(self.check_out_location_id) if self.check_out_location_id else None,
            "workLocationStatus": self.work_location_status,
            "isLate": self.is_late,
            "totalHours": float(self.total_hours) if self.total_hours else None,
            "checkInDevice": self.check_in_device,
            "checkOutDevice": self.check_out_device,
            "status": self.status,
            "isManualEntry": self.is_manual_entry,
            "isDeleted": self.is_deleted,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }
