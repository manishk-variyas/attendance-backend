import uuid
from sqlalchemy import Column, String, Date, Integer, DateTime, text
from sqlalchemy.dialects.postgresql import UUID
from app.core.models import Base


class WfhRequest(Base):
    __tablename__ = "wfh_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    keycloak_user_id = Column(String(255), nullable=False, index=True)
    user_email = Column(String(255), nullable=False, index=True)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    resuming_date = Column(Date, nullable=True)
    reason = Column(String(500), nullable=True)
    comment = Column(String(500), nullable=True)
    contact_number = Column(String(50), nullable=True)
    approver_id = Column(Integer, nullable=True)
    project_id = Column(Integer, nullable=True)
    project_name = Column(String(255), nullable=True)
    status = Column(String(20), nullable=False, server_default=text("'pending'"))
    reviewed_by = Column(String(255), nullable=True)
    reject_reason = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "keycloakUserId": self.keycloak_user_id,
            "userEmail": self.user_email,
            "startDate": self.start_date.isoformat() if self.start_date else None,
            "endDate": self.end_date.isoformat() if self.end_date else None,
            "resumingDate": self.resuming_date.isoformat() if self.resuming_date else None,
            "reason": self.reason,
            "comment": self.comment,
            "contactNumber": self.contact_number,
            "approverId": self.approver_id,
            "projectId": self.project_id,
            "projectName": self.project_name,
            "status": self.status,
            "reviewedBy": self.reviewed_by,
            "rejectReason": self.reject_reason,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }
