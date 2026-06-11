import uuid
from sqlalchemy import Column, String, Integer, Float, DateTime, text
from sqlalchemy.dialects.postgresql import UUID
from app.core.models import Base


class LeaveBalance(Base):
    __tablename__ = "leave_balances"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    keycloak_user_id = Column(String(255), nullable=False, index=True)
    year = Column(Integer, nullable=False, server_default=text("EXTRACT(year FROM now())"))
    total_earned = Column(Float, nullable=False, server_default=text("12.0"))
    used_earned = Column(Float, nullable=False, server_default=text("0.0"))
    accrued_compoff = Column(Float, nullable=False, server_default=text("0.0"))
    consumed_compoff = Column(Float, nullable=False, server_default=text("0.0"))
    unpaid = Column(Float, nullable=False, server_default=text("0.0"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "user_id": self.keycloak_user_id,
            "year": self.year,
            "total_earned": self.total_earned,
            "used_earned": self.used_earned,
            "accrued_compoff": self.accrued_compoff,
            "consumed_compoff": self.consumed_compoff,
            "unpaid": self.unpaid,
        }
