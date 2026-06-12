import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Boolean, DateTime, text
from sqlalchemy.dialects.postgresql import UUID
from app.core.models import Base


class EmployeeStatus:
    PENDING = "pending"
    ACTIVE = "active"
    DISABLED = "disabled"


class EmployeeMaster(Base):
    __tablename__ = "employee_master"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    keycloak_user_id = Column(String(255), nullable=True, unique=True)
    redmine_user_id = Column(Integer, nullable=True, unique=True)
    first_name = Column(String(255), nullable=False)
    middle_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=False)
    user_email = Column(String(255), nullable=False, unique=True)
    designation = Column(String(255), nullable=True)
    employee_id = Column(String(100), nullable=True, unique=True)
    work_location_status = Column(String(50), nullable=True)
    work_address = Column(String(500), server_default=text("'N/A'"))
    home_address = Column(String(500), nullable=True)
    contact_number = Column(String(50), nullable=True)
    alt_contact_number = Column(String(50), nullable=True)
    country = Column(String(100), server_default=text("''"))
    status = Column(String(20), nullable=False, server_default=text("'active'"))
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    onboarded_at = Column(DateTime(timezone=True), nullable=True)
    location_id = Column(UUID(as_uuid=True), nullable=True)
    reports_to = Column(Integer, nullable=True)
    reports_to_name = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
