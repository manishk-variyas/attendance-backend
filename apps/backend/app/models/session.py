from datetime import datetime
from sqlalchemy import Column, String, DateTime, text
from sqlalchemy.dialects.postgresql import JSONB
from app.core.models import Base


class Session(Base):
    __tablename__ = "sessions"

    id = Column(String(64), primary_key=True)
    user_sub = Column(String(255), nullable=False, index=True)
    data = Column(JSONB, nullable=False, server_default=text("'{}'"))
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
