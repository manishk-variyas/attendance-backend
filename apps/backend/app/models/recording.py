import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Boolean, DateTime, text
from sqlalchemy.dialects.postgresql import UUID
from app.core.models import Base


class Recording(Base):
    __tablename__ = "recordings"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    keycloak_user_id = Column(String(255), nullable=False)
    user_email = Column(String(255), nullable=False, index=True)
    ticket_id = Column(Integer, nullable=True)
    project = Column(String(255), nullable=True)
    priority = Column(String(50), nullable=True)
    status = Column(String(50), nullable=True)
    filename = Column(String(500), nullable=False)
    recording_url = Column(String(1000), nullable=False)
    is_played = Column(Boolean, nullable=False, server_default=text("false"))
    is_deleted = Column(Boolean, nullable=False, server_default=text("false"))
    deletion_date = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "user_email": self.user_email,
            "keycloak_user_id": self.keycloak_user_id,
            "ticket_id": self.ticket_id,
            "project": self.project,
            "priority": self.priority,
            "status": self.status,
            "filename": self.filename,
            "recording_url": self.recording_url,
            "is_played": self.is_played,
            "is_deleted": self.is_deleted,
            "deletion_date": self.deletion_date,
            "deleted_by": self.deleted_by,
            "created_at": self.created_at,
        }
