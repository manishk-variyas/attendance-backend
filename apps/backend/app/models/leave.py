import uuid
import enum
from datetime import datetime
from sqlalchemy import Column, String, Integer, Date, Boolean, DateTime, text
from sqlalchemy.dialects.postgresql import UUID, JSON
from app.core.models import Base


class LeaveType(str, enum.Enum):
    EL = "EL"
    UPL = "UPL"
    PL = "PL"


class LeaveStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EMERGENCY = "emergency"
    CANCELLED = "cancelled"


class Leave(Base):
    __tablename__ = "leaves"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    keycloak_user_id = Column(String(255), nullable=False, index=True)
    user_email = Column(String(255), nullable=False, index=True)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    leave_type = Column(String(3), nullable=False)
    reason = Column(String(1000), nullable=True)
    comment = Column(String(1000), nullable=True)
    is_traveling = Column(Boolean, nullable=True)
    contact_number = Column(String(50), nullable=True)
    resuming_date = Column(Date, nullable=True)
    leave_dates = Column(JSON, nullable=True)
    approver_id = Column(Integer, nullable=True)
    approval_status = Column(String(8), nullable=False, server_default=text("'pending'"))
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approved_by = Column(String(255), nullable=True)
    rejected_at = Column(DateTime(timezone=True), nullable=True)
    rejected_by = Column(String(255), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_by = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "user_id": self.keycloak_user_id,
            "user_email": self.user_email,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "leave_type": self.leave_type,
            "reason": self.reason,
            "comment": self.comment,
            "is_traveling": self.is_traveling,
            "contact_number": self.contact_number,
            "resuming_date": self.resuming_date.isoformat() if self.resuming_date else None,
            "leave_dates": self.leave_dates if self.leave_dates else None,
            "approver_id": self.approver_id,
            "status": self.approval_status,
            "approved_at": self.approved_at,
            "approved_by": self.approved_by,
            "rejected_at": self.rejected_at,
            "rejected_by": self.rejected_by,
            "cancelled_at": self.cancelled_at,
            "cancelled_by": self.cancelled_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
