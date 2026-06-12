from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class EmployeeCreate(BaseModel):
    first_name: str
    middle_name: Optional[str] = None
    last_name: str
    user_email: str
    designation: Optional[str] = None
    employee_id: Optional[str] = None
    work_location_status: Optional[str] = None
    work_address: Optional[str] = "N/A"
    home_address: Optional[str] = None
    contact_number: Optional[str] = None
    alt_contact_number: Optional[str] = None
    country: Optional[str] = ""
    reports_to: Optional[int] = None


class EmployeeResponse(BaseModel):
    id: str
    keycloak_user_id: Optional[str] = None
    redmine_user_id: Optional[int] = None
    first_name: str
    middle_name: Optional[str] = None
    last_name: str
    user_email: str
    designation: Optional[str] = None
    employee_id: Optional[str] = None
    work_location_status: Optional[str] = None
    work_address: Optional[str] = None
    home_address: Optional[str] = None
    contact_number: Optional[str] = None
    alt_contact_number: Optional[str] = None
    country: Optional[str] = None
    status: str
    onboarded_at: Optional[datetime] = None
    location_id: Optional[str] = None
    reports_to: Optional[int] = None
    reports_to_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class EmployeeOnboardRequest(BaseModel):
    email: str
    keycloak_user_id: str


class EmployeeStatusUpdate(BaseModel):
    status: str


class EmployeeLocationUpdate(BaseModel):
    location_id: str  # UUID of the office_location


class EmployeeActiveToggle(BaseModel):
    is_active: bool


class AssignProjectRequest(BaseModel):
    user_email: str
    project_id: int
    reports_to: int
