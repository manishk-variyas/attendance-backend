import uuid

from sqlalchemy import Boolean, Column, DateTime, String, text
from sqlalchemy.dialects.postgresql import UUID

from app.core.models import Base


class State(Base):
    """Master list of states / union territories (used to scope holidays).

    `code` is the canonical key (ISO 3166-2 subdivision, e.g. `IN-UP`) that
    other tables reference. `gst_code` is kept for future payroll use.
    """

    __tablename__ = "states"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    country_code = Column(String(2), nullable=False)
    code = Column(String(10), nullable=False, unique=True)
    short_code = Column(String(10), nullable=True)
    gst_code = Column(String(2), nullable=True)
    name = Column(String(100), nullable=False)
    type = Column(String(20), nullable=False, server_default=text("'state'"))
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"), onupdate=text("now()"))

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "country_code": self.country_code,
            "code": self.code,
            "short_code": self.short_code,
            "gst_code": self.gst_code,
            "name": self.name,
            "type": self.type,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
