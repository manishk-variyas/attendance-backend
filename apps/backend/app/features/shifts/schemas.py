from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class ShiftCreate(BaseModel):
    userId: int
    userName: str
    userEmail: str
    projectId: int
    projectName: str
    shift: str                      # e.g. "general", "morning", "night"
    workStatus: str                 # e.g. "OFFICE", "WFH", "PRESENT"
    workAddress: Optional[str] = "N/A"
    leaveType: Optional[str] = None
    date: str                       # "YYYY-MM-DD"
    endDate: Optional[str] = None   # "YYYY-MM-DD"
    shiftStartTime: str             # "HH:MM"
    shiftEndTime: str               # "HH:MM"
    shiftStartUTC: Optional[datetime] = None
    shiftEndUTC: Optional[datetime] = None
    shiftName: str
    timezone: str = "Asia/Kolkata"
    country: str = ""


class ShiftBulkCreate(BaseModel):
    """Create shifts across a date range. One shift per day will be created,
    duplicates (same userId + date) are automatically skipped."""
    userId: int
    userName: str
    userEmail: str
    projectId: int
    projectName: str
    shift: str           # "morning" | "general" | "afternoon" | "night" | "Not in Any Shift"
    workStatus: str      # "WFH" | "OFFICE" | "CUSTOMER_SITE" | "LEAVE"
    workAddress: Optional[str] = "N/A"
    leaveType: Optional[str] = None
    startDate: str       # "YYYY-MM-DD"  — first day (inclusive)
    endDate: str         # "YYYY-MM-DD"  — last day  (inclusive, same as startDate for single day)
    shiftStartTime: str  # "HH:MM"
    shiftEndTime: str    # "HH:MM"


class ShiftUpdate(BaseModel):
    shift: Optional[str] = None
    workStatus: Optional[str] = None
    workAddress: Optional[str] = None
    leaveType: Optional[str] = None
    shiftStartTime: Optional[str] = None
    shiftEndTime: Optional[str] = None
    shiftStartUTC: Optional[datetime] = None
    shiftEndUTC: Optional[datetime] = None
    projectId: Optional[int] = None
    projectName: Optional[str] = None
    endDate: Optional[str] = None


class ShiftResponse(BaseModel):
    id: str = Field(alias="_id")
    userId: int
    userName: str
    userEmail: str
    projectId: int
    projectName: str
    shift: str
    workStatus: str
    workAddress: Optional[str] = None
    leaveType: Optional[str] = None
    date: str
    endDate: Optional[str] = None
    shiftStartTime: str
    shiftEndTime: str
    shiftStartUTC: Optional[datetime] = None
    shiftEndUTC: Optional[datetime] = None
    createdAt: Optional[datetime] = None
    updatedAt: Optional[datetime] = None

    class Config:
        populate_by_name = True
        from_attributes = True


class ShiftStats(BaseModel):
    total_shifts: int
    active_shifts: int
    completed_shifts: int
    leave_shifts: int
    users_with_shifts: int


class ShiftHistoryItem(BaseModel):
    id: str
    userId: Optional[int] = None
    userName: Optional[str] = None
    userEmail: str
    projectId: Optional[int] = None
    projectName: Optional[str] = None
    shift: str
    workStatus: str
    workAddress: Optional[str] = None
    leaveType: Optional[str] = None
    date: str
    endDate: Optional[str] = None
    shiftStartTime: Optional[str] = None
    shiftEndTime: Optional[str] = None
    createdAt: Optional[datetime] = None


class ShiftDefinitionCreate(BaseModel):
    shiftCode: str
    shiftName: str
    startTime: str
    endTime: str
    timezone: str = "Asia/Kolkata"
    country: str = ""


class ShiftDefinitionResponse(BaseModel):
    id: str = Field(alias="_id")
    shiftCode: str
    shiftName: str
    startTime: str
    endTime: str
    timezone: str
    country: str
    createdAt: Optional[datetime] = None
    updatedAt: Optional[datetime] = None

   