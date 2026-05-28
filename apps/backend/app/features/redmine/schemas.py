from pydantic import BaseModel, EmailStr
from typing import List, Optional

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


class ProjectMember(BaseModel):
    user_id: int
    name: str
    email: str
    roles: List[str]

class TimeZoneInfo(BaseModel):
    value: str
    label: str
    offset: str
