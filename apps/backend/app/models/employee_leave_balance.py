import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, DateTime, ForeignKey, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from app.core.models import Base


class EmployeeLeaveBalance(Base):
    __tablename__ = "employee_leave_balances"
    __table_args__ = (
        UniqueConstraint("keycloak_user_id", "leave_type_id", "fiscal_year", name="uq_elb_user_type_year"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    keycloak_user_id = Column(String(255), nullable=False, index=True)
    leave_type_id = Column(UUID(as_uuid=True), ForeignKey("leave_types.id"), nullable=False)
    fiscal_year = Column(Integer, nullable=False)          # e.g. 2026 = Apr 2026 - Mar 2027
    carry_forward = Column(Float, nullable=False, server_default=text("0"))
    adjustment = Column(Float, nullable=False, server_default=text("0"))
    modified_by = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class EmployeeLeaveBalanceMonthly(Base):
    __tablename__ = "employee_leave_balance_monthly"
    __table_args__ = (
        UniqueConstraint("leave_balance_id", "month", name="uq_elbm_balance_month"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    leave_balance_id = Column(UUID(as_uuid=True), ForeignKey("employee_leave_balances.id", ondelete="CASCADE"), nullable=False)
    month = Column(Integer, nullable=False)                # 1 = Apr ... 12 = Mar
    accrued = Column(Float, nullable=False, server_default=text("0"))
