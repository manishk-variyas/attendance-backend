from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime
from app.features.redmine.constants import REDMINE_TO_IANA_TZ


class EmployeeCreate(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=100)
    middle_name: Optional[str] = Field(None, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    user_email: str
    designation: Optional[str] = Field(None, max_length=100)
    employee_id: Optional[str] = Field(None, max_length=50)
    work_location_status: Optional[str] = None
    work_address: Optional[str] = "N/A"
    current_address: Optional[str] = None
    permanent_address: Optional[str] = None
    contact_number: Optional[str] = Field(None, max_length=15)
    alt_contact_number: Optional[str] = Field(None, max_length=15)
    country: Optional[str] = Field("", max_length=50)
    reports_to: Optional[int] = None
    location_id: Optional[str] = None
    joining_date: Optional[str] = None
    title: Optional[str] = Field(None, max_length=10)


class UserProfileResponse(BaseModel):
    first_name: str
    middle_name: Optional[str] = None
    last_name: str
    user_email: str
    designation: Optional[str] = None
    employee_id: Optional[str] = None
    work_location_status: Optional[str] = None
    contact_number: Optional[str] = None
    alt_contact_number: Optional[str] = None
    work_address: Optional[str] = None
    current_address: Optional[str] = None
    permanent_address: Optional[str] = None
    reports_to_name: Optional[str] = None
    is_active: bool
    joined_at: Optional[str] = None
    profilePictureUrl: Optional[str] = None


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
    current_address: Optional[str] = None
    permanent_address: Optional[str] = None
    contact_number: Optional[str] = None
    alt_contact_number: Optional[str] = None
    country: Optional[str] = None
    status: str
    onboarded_at: Optional[datetime] = None
    location_id: Optional[str] = None
    reports_to: Optional[int] = None
    reports_to_name: Optional[str] = None
    joining_date: Optional[str] = None
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
    redmine_role_name: Optional[str] = "Developer"


class EmployeeUpdate(BaseModel):
    first_name: Optional[str] = Field(None, max_length=100)
    middle_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    user_email: Optional[str] = None
    designation: Optional[str] = Field(None, max_length=100)
    contact_number: Optional[str] = Field(None, max_length=15)
    alt_contact_number: Optional[str] = Field(None, max_length=15)
    work_address: Optional[str] = None
    current_address: Optional[str] = None
    permanent_address: Optional[str] = None
    work_location_status: Optional[str] = None
    country: Optional[str] = Field(None, max_length=50)
    timezone: Optional[str] = None
    location_id: Optional[str] = None
    reports_to: Optional[int] = None
    joining_date: Optional[str] = None
    title: Optional[str] = Field(None, max_length=10)
    project_id: Optional[int] = None
    redmine_role_name: Optional[str] = None
    keycloak_role_name: Optional[str] = None

    @field_validator("timezone")
    @classmethod
    def coerce_timezone(cls, v: str) -> str:
        return REDMINE_TO_IANA_TZ.get(v, v) if v else v


class DisonboardRequest(BaseModel):
    project_id: int
