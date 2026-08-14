import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, Float, DateTime, text
from sqlalchemy.dialects.postgresql import UUID
from app.core.models import Base


class LeaveTypeConfig(Base):
    __tablename__ = "leave_types"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    code = Column(String(20), nullable=False, unique=True)
    name = Column(String(100), nullable=False, unique=True)
    is_paid = Column(Boolean, nullable=False, server_default=text("true"))
    carry_forward_allowed = Column(Boolean, nullable=False, server_default=text("false"))
    carry_forward_cap = Column(Float, nullable=True)
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "code": self.code,
            "name": self.name,
            "is_paid": self.is_paid,
            "carry_forward_allowed": self.carry_forward_allowed,
            "carry_forward_cap": self.carry_forward_cap,
            "is_active": self.is_active,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
