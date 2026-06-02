from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Optional
from enum import Enum

class LeaveType(str, Enum):
    EL = "EL"   # Earned Leave
    UPL = "UPL" # Unpaid Leave
    PL = "PL"   # Paid Leave

class LeaveStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

class LeaveApplyRequest(BaseModel):
    start_date: datetime
    end_date: datetime
    leave_type: LeaveType
    reason: Optional[str] = None
    comment: Optional[str] = None
    is_traveling: Optional[bool] = None
    contact_number: Optional[str] = None
    resuming_date: Optional[datetime] = None
    leave_dates: Optional[List[datetime]] = None
    approver_id: Optional[int] = None

class LeaveHistoryItem(BaseModel):
    id: str
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    start_date: datetime
    end_date: datetime
    leave_type: LeaveType
    reason: Optional[str] = None
    comment: Optional[str] = None
    is_traveling: Optional[bool] = None
    contact_number: Optional[str] = None
    resuming_date: Optional[datetime] = None
    leave_dates: Optional[List[datetime]] = None
    status: LeaveStatus
    approver_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    rejected_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    rejected_by: Optional[str] = None
    approved_by_role: Optional[str] = None
    rejected_by_role: Optional[str] = None

class LeaveStats(BaseModel):
    total_earned: float = 0.0
    used_earned: float = 0.0
    total_paid: float = 0.0
    used_paid: float = 0.0
    total_unpaid: float = 0.0
    used_unpaid: float = 0.0
    pending_applications: int = 0

class HolidayType(str, Enum):
    GAZETTED = "GAZETTED"
    RESTRICTED = "RESTRICTED"

class Holiday(BaseModel):
    country_code: str
    region: Optional[str] = None
    holiday_date: str
    holiday_name: str
    holiday_type: HolidayType
    is_national: bool

