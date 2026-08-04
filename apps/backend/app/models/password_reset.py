from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Boolean, text
from app.core.models import Base


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(String(64), primary_key=True)
    user_sub = Column(String(255), nullable=False, index=True)
    token_hash = Column(String(128), nullable=False, unique=True)
    used = Column(Boolean, nullable=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    expires_at = Column(DateTime(timezone=True), nullable=False)
    reset_attempts = Column(Integer, nullable=False, server_default=text("0"))
