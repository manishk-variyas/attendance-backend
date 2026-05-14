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

class LeaveHistoryItem(BaseModel):
    id: str
    start_date: datetime
    end_date: datetime
    leave_type: LeaveType
    status: LeaveStatus

class LeaveStats(BaseModel):
    total_earned: float = 0.0
    used_earned: float = 0.0
    total_paid: float = 0.0
    used_paid: float = 0.0
    total_unpaid: float = 0.0
    used_unpaid: float = 0.0
    pending_applications: int = 0

class HolidayItem(BaseModel):
    date: datetime
    name: str
    description: Optional[str] = None
