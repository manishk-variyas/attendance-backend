import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Boolean, text
from sqlalchemy.dialects.postgresql import JSONB
from app.core.models import Base


class Session(Base):
    __tablename__ = "sessions"

    id = Column(String(64), primary_key=True)
    user_sub = Column(String(255), nullable=False, index=True)
    data = Column(JSONB, nullable=False, server_default=text("'{}'"))
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    last_activity_at = Column(DateTime(timezone=True), nullable=True)
    # Soft-delete columns: keep history, mark inactive for auth
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    logged_out_at = Column(DateTime(timezone=True), nullable=True)
    # Opaque, non-secret public identifier safe to expose via admin APIs
    display_id = Column(String(36), nullable=True, unique=True, index=True)
