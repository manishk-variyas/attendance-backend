from datetime import datetime
from sqlalchemy import Column, String, DateTime, Integer, Boolean, Text, text
from app.core.models import Base


class DeleteVerificationToken(Base):
    __tablename__ = "delete_verification_tokens"

    id = Column(String(64), primary_key=True)
    admin_sub = Column(String(255), nullable=False, index=True)
    admin_email = Column(String(255), nullable=False)
    otp_hash = Column(String(128), nullable=False)
    ids_json = Column(Text, nullable=False)
    force = Column(Boolean, nullable=False, server_default=text("false"))
    attempts = Column(Integer, nullable=False, server_default=text("0"))
    used = Column(Boolean, nullable=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    expires_at = Column(DateTime(timezone=True), nullable=False)
