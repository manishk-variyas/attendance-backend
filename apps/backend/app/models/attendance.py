import uuid
from datetime import datetime, date
from sqlalchemy import Column, String, Date, DateTime, Numeric, Boolean, Enum as SAEnum, UniqueConstraint, CheckConstraint, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID
from app.core.models import Base


class Attendance(Base):
    __tablename__ = "attendance"

    __table_args__ = (
        UniqueConstraint("keycloak_user_id", "attendance_date", name="uq_attendance_employee_date"),
        CheckConstraint(
            "check_out_time IS NULL OR check_in_time IS NULL OR check_out_time >= check_in_time",
            name="chk_attendance_time_order",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    keycloak_user_id = Column(String(255), nullable=False, index=True)
    attendance_date = Column(Date, nullable=False, index=True)
    check_in_time = Column(DateTime(timezone=True), nullable=True)
    check_in_lat = Column(Numeric(10, 8), nullable=True)
    check_in_lng = Column(Numeric(11, 8), nullable=True)
    check_in_location_name = Column(String(500), nullable=True)
    check_out_time = Column(DateTime(timezone=True), nullable=True)
    check_out_lat = Column(Numeric(10, 8), nullable=True)
    check_out_lng = Column(Numeric(11, 8), nullable=True)
    check_out_location_name = Column(String(500), nullable=True)
    check_in_device = Column(String(100), nullable=True)
    check_out_device = Column(String(100), nullable=True)
    location_id = Column(UUID(as_uuid=True), nullable=True)
    shift_id = Column(UUID(as_uuid=True), ForeignKey("shifts.id", ondelete="SET NULL"), nullable=True)
    actual_location_id = Column(UUID(as_uuid=True), ForeignKey("office_locations.id", ondelete="SET NULL"), nullable=True)
    work_location_status = Column(SAEnum('OFFICE', 'WFH', 'REMOTE_ONSITE', 'REMOTE_OFFSITE', 'OUT_OF_OFFICE', 'LEAVE', name='work_location', create_type=False), nullable=False, server_default=text("'OFFICE'"))
    per_diem_eligible = Column(Boolean, nullable=False, server_default=text("false"))
    conveyance_eligible = Column(Boolean, nullable=False, server_default=text("false"))
    shift_code = Column(String(50), nullable=True)
    total_hours = Column(Numeric(5, 2), nullable=True)
    is_late = Column(Boolean, nullable=False, server_default=text("false"))
    status = Column(SAEnum('present', 'absent', 'half_day', 'on_leave', 'holiday', name='attendance_status', create_type=False), nullable=False, server_default=text("'present'"))
    is_manual_entry = Column(Boolean, nullable=False, server_default=text("false"))
    is_synced = Column(Boolean, nullable=False, server_default=text("false"))
    server_received_at = Column(DateTime(timezone=True), nullable=True)
    modified_by = Column(String(255), nullable=True)
    remarks = Column(String(500), nullable=True)
    comp_off_credited = Column(Boolean, nullable=False, server_default=text("false"))
    checkout_reminder_sent = Column(Boolean, nullable=False, server_default=text("false"))
    is_deleted = Column(Boolean, nullable=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "keycloakUserId": self.keycloak_user_id,
            "attendanceDate": self.attendance_date.isoformat() if self.attendance_date else None,
            "checkInTime": self.check_in_time.isoformat() if self.check_in_time else None,
            "checkInLat": float(self.check_in_lat) if self.check_in_lat else None,
            "checkInLng": float(self.check_in_lng) if self.check_in_lng else None,
            "checkInLocationName": self.check_in_location_name,
            "checkOutTime": self.check_out_time.isoformat() if self.check_out_time else None,
            "checkOutLat": float(self.check_out_lat) if self.check_out_lat else None,
            "checkOutLng": float(self.check_out_lng) if self.check_out_lng else None,
            "checkOutLocationName": self.check_out_location_name,
            "checkInDevice": self.check_in_device,
            "checkOutDevice": self.check_out_device,
            "locationId": str(self.location_id) if self.location_id else None,
            "expectedLocationId": str(self.location_id) if self.location_id else None,
            "shiftId": str(self.shift_id) if self.shift_id else None,
            "actualLocationId": str(self.actual_location_id) if self.actual_location_id else None,
            "locationMismatch": bool(
                self.location_id and self.actual_location_id
                and self.location_id != self.actual_location_id
            ),
            "workLocationStatus": self.work_location_status,
            "perDiemEligible": self.per_diem_eligible,
            "conveyanceEligible": self.conveyance_eligible,
            "shiftCode": self.shift_code,
            "totalHours": float(self.total_hours) if self.total_hours else None,
            "isLate": self.is_late,
            "status": self.status,
            "isManualEntry": self.is_manual_entry,
            "isSynced": self.is_synced,
            "serverReceivedAt": self.server_received_at.isoformat() if self.server_received_at else None,
            "modifiedBy": self.modified_by,
            "remarks": self.remarks,
            "isDeleted": self.is_deleted,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }
