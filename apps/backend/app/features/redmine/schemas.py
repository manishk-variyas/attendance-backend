import re
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from typing import List, Optional, Any
from datetime import datetime


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_date(v: str | None) -> str | None:
    if v is not None and not _DATE_RE.match(v):
        raise ValueError("Must be in YYYY-MM-DD format")
    return v


def _validate_custom_fields(v: list[dict] | None) -> list[dict] | None:
    if v is None:
        return v
    for i, cf in enumerate(v):
        if not isinstance(cf, dict):
            raise ValueError(f"custom_fields[{i}] must be a dict")
        if "id" not in cf or not isinstance(cf["id"], int):
            raise ValueError(f"custom_fields[{i}].id is required and must be an int")
        if "value" not in cf:
            raise ValueError(f"custom_fields[{i}].value is required")
    return v


class ProjectBase(BaseModel):
    customerName: str
    city: str
    customerOfficeLocation: str
    projectType: str
    status: str

class ProjectCreate(ProjectBase):
    email: EmailStr

class ProjectResponse(ProjectBase):
    id: int
    name: str
    identifier: str

class UserProjectResponse(BaseModel):
    email: str
    projects: List[ProjectResponse]

class UserWithProjects(BaseModel):
    id: int
    firstname: str
    lastname: str
    mail: str
    projects: List[ProjectResponse]

class IssueResponse(BaseModel):
    id: int
    subject: str
    description: Optional[str]
    status: str
    priority: str
    tracker: str
    project_id: int
    project_name: str
    assigned_to_name: Optional[str] = None
    created_on: str
    updated_on: str
    start_date: Optional[str] = None
    due_date: Optional[str] = None
    estimated_hours: Optional[float] = None
    done_ratio: int = 0
    is_private: bool = False
    status_id: int
    priority_id: int
    tracker_id: int
    assigned_to_id: Optional[int] = None
    author_id: int
    author_name: str
    attachments: list[dict] = []
    custom_fields: list[dict] = []


class ProjectMember(BaseModel):
    user_id: int
    name: str
    email: str
    roles: List[str]

class TimeZoneInfo(BaseModel):
    value: str
    label: str
    offset: str


class ProjectSearchItem(BaseModel):
    id: str
    name: str
    identifier: str
    type: str
    status: str
    memberCount: int


class PersonSearchItem(BaseModel):
    id: str
    name: str
    role: str
    projectName: str


class ShiftSearchItem(BaseModel):
    id: str
    date: str
    startTime: str
    endTime: str
    status: str
    projectName: str
    employeeName: str


class SearchResults(BaseModel):
    projects: List[ProjectSearchItem] = []
    people: List[PersonSearchItem] = []
    shifts: List[ShiftSearchItem] = []


class SearchResponse(BaseModel):
    query: str
    results: SearchResults


class IssueCreate(BaseModel):
    project_id: int
    subject: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    tracker_id: int | None = None
    status_id: int | None = None
    priority_id: int | None = None
    assigned_to_id: int | None = None
    category_id: int | None = None
    fixed_version_id: int | None = None
    parent_issue_id: int | None = None
    start_date: str | None = None
    due_date: str | None = None
    estimated_hours: float | None = None
    is_private: bool | None = None
    watcher_user_ids: list[int] | None = None
    custom_fields: list[dict] | None = None

    @field_validator("start_date", "due_date")
    @classmethod
    def date_format(cls, v):
        return _validate_date(v)

    @field_validator("custom_fields")
    @classmethod
    def check_custom_fields(cls, v):
        return _validate_custom_fields(v)

    @field_validator("estimated_hours")
    @classmethod
    def check_estimated_hours(cls, v):
        if v is not None and v < 0:
            raise ValueError("Must be >= 0")
        return v

    @model_validator(mode="after")
    def check_dates(self):
        if self.start_date and self.due_date and self.start_date > self.due_date:
            raise ValueError("due_date must be >= start_date")
        return self


class IssueUpdate(BaseModel):
    subject: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    tracker_id: int | None = None
    status_id: int | None = None
    priority_id: int | None = None
    assigned_to_id: int | None = None
    category_id: int | None = None
    fixed_version_id: int | None = None
    parent_issue_id: int | None = None
    start_date: str | None = None
    due_date: str | None = None
    estimated_hours: float | None = None
    is_private: bool | None = None
    watcher_user_ids: list[int] | None = None
    custom_fields: list[dict] | None = None
    notes: str | None = None
    private_notes: bool | None = None

    @field_validator("start_date", "due_date")
    @classmethod
    def date_format(cls, v):
        return _validate_date(v)

    @field_validator("custom_fields")
    @classmethod
    def check_custom_fields(cls, v):
        return _validate_custom_fields(v)

    @field_validator("estimated_hours")
    @classmethod
    def check_estimated_hours(cls, v):
        if v is not None and v < 0:
            raise ValueError("Must be >= 0")
        return v

    @model_validator(mode="after")
    def check_dates(self):
        if self.start_date and self.due_date and self.start_date > self.due_date:
            raise ValueError("due_date must be >= start_date")
        return self


# ── Redmine Issue Response Models ──────────────────────────────────────

class IssueCreatedResponse(BaseModel):
    id: int
    subject: str
    status_id: int
    status_name: str
    tracker_id: int
    tracker_name: str
    priority_id: int
    priority_name: str
    project_id: int
    project_name: str
    author_id: int
    author_name: str
    assigned_to_id: int | None = None
    assigned_to_name: str | None = None
    start_date: str | None = None
    due_date: str | None = None
    estimated_hours: float | None = None
    is_private: bool = False
    description: str | None = None
    created_on: str
    updated_on: str
