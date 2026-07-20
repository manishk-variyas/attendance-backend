from pydantic import BaseModel, Field
from datetime import datetime, date
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
    EMERGENCY = "emergency"
    CANCELLED = "cancelled"

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
    userName: Optional[str] = None
    userDesignation: Optional[str] = None
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
    holiday_date: date
    holiday_name: str
    holiday_type: HolidayType
    is_national: bool


class BatchLeaveRequest(BaseModel):
    leave_ids: List[str]


class BatchLeaveResult(BaseModel):
    leave_id: str
    status: str

class BatchLeaveResponse(BaseModel):
    processed: int
    approved: int = 0
    rejected: int = 0
    failed: int = 0
    results: List[BatchLeaveResult]


class LeaveBalanceCreate(BaseModel):
    user_email: str = Field(..., description="Employee's email — resolves to their keycloak_user_id")
    year: int = Field(default_factory=lambda: datetime.now().year)
    total_earned: float = 12.0
    used_earned: float = 0.0
    accrued_compoff: float = 0.0
    consumed_compoff: float = 0.0
    unpaid: float = 0.0


class LeaveBalanceUpdate(BaseModel):
    year: Optional[int] = None
    total_earned: Optional[float] = None
    used_earned: Optional[float] = None
    accrued_compoff: Optional[float] = None
    consumed_compoff: Optional[float] = None
    unpaid: Optional[float] = None


class LeaveBalanceResponse(BaseModel):
    id: str
    keycloak_user_id: str
    year: int
    total_earned: float
    used_earned: float
    accrued_compoff: float
    consumed_compoff: float
    unpaid: float
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

