from pydantic import BaseModel, Field, field_validator
from datetime import datetime, date
from typing import List, Optional, Dict
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
    CANCELLATION_REQUESTED = "cancellation_requested"
    CANCELLATION_REJECTED = "cancellation_rejected"

class LeaveApplyRequest(BaseModel):
    start_date: datetime
    end_date: datetime
    leave_type: str = Field(..., min_length=1, max_length=20)
    reason: Optional[str] = None
    comment: Optional[str] = None
    is_traveling: Optional[bool] = None
    contact_number: Optional[str] = None
    resuming_date: Optional[datetime] = None
    leave_dates: Optional[List[datetime]] = None
    approver_id: Optional[int] = None

    @field_validator("leave_type")
    @classmethod
    def upper_leave_type(cls, v: str) -> str:
        return v.strip().upper()

class LeaveHistoryItem(BaseModel):
    id: str
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    userName: Optional[str] = None
    userDesignation: Optional[str] = None
    start_date: datetime
    end_date: datetime
    leave_type: str
    leave_type_id: Optional[str] = None
    leave_type_name: Optional[str] = None
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
    cancellation_remark: Optional[str] = None
    cancellation_requested_at: Optional[datetime] = None
    cancellation_requested_by: Optional[str] = None
    cancellation_rejected_at: Optional[datetime] = None
    cancellation_rejected_by: Optional[str] = None
    cancellation_rejection_remark: Optional[str] = None
    cancellation_attempts: Optional[int] = None

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


class CancelLeaveRequest(BaseModel):
    remark: str = Field(..., min_length=1, max_length=1000, description="Reason for requesting leave cancellation")


class CancelLeaveRejectRequest(BaseModel):
    remark: str = Field(..., min_length=1, max_length=1000, description="Reason for rejecting the cancellation request")


class BatchCancelLeaveRejectRequest(BaseModel):
    leave_ids: List[str]
    remark: str = Field(..., min_length=1, max_length=1000, description="Reason for rejecting the cancellation requests")


class LeaveTypeCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=20)
    name: str = Field(..., min_length=1, max_length=100)
    is_paid: bool = True
    carry_forward_allowed: bool = False
    carry_forward_cap: Optional[float] = Field(None, ge=0)

    @field_validator("code")
    @classmethod
    def upper_code(cls, v: str) -> str:
        return v.strip().upper()


class LeaveTypeUpdate(BaseModel):
    code: Optional[str] = Field(None, min_length=1, max_length=20)
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    is_paid: Optional[bool] = None
    carry_forward_allowed: Optional[bool] = None
    carry_forward_cap: Optional[float] = Field(None, ge=0)
    is_active: Optional[bool] = None

    @field_validator("code")
    @classmethod
    def upper_code(cls, v: str) -> str:
        return v.strip().upper() if v else v


class LeaveTypeResponse(BaseModel):
    id: str
    code: str
    name: str
    is_paid: bool
    carry_forward_allowed: bool
    carry_forward_cap: Optional[float] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LeaveTypeOption(BaseModel):
    id: str
    code: str
    name: str
    is_paid: bool


class LeaveTypeBalanceEntry(BaseModel):
    code: str = Field(..., min_length=1, max_length=20)
    carry_forward: float = Field(0, ge=0)
    adjustment: float = Field(0)
    months: Dict[int, float] = Field(default_factory=dict)

    @field_validator("code")
    @classmethod
    def upper_code(cls, v: str) -> str:
        return v.strip().upper()


class EmployeeLeaveBalanceBulkCreate(BaseModel):
    id: str = Field(..., description="EmployeeMaster primary key (UUID)")
    fiscal_year: int = Field(..., ge=2000)
    types: List[LeaveTypeBalanceEntry]


class LeaveBalanceSummaryItem(BaseModel):
    code: str
    name: str
    total: float = 0.0
    availed: float = 0.0
    balance: float = 0.0


class EmployeeLeaveBalanceSummary(BaseModel):
    id: Optional[str] = None
    employee_id: Optional[str] = None
    as_of_date: str
    leave_balances: List[LeaveBalanceSummaryItem]
    total_available_leave: float = 0.0

