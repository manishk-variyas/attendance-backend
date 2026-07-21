import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, text
from sqlalchemy.dialects.postgresql import UUID
from app.core.models import Base


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id = Column(String(255), nullable=False, index=True)
    type = Column(String(50), nullable=False)
    message = Column(String(1000), nullable=False)
    from_user = Column(String(255), nullable=False)
    reference_id = Column(String(255), nullable=True)
    is_read = Column(Boolean, nullable=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "user_id": self.user_id,
            "type": self.type,
            "message": self.message,
            "from_user": self.from_user,
            "reference_id": self.reference_id,
            "is_read": self.is_read,
            "created_at": self.created_at,
        }
