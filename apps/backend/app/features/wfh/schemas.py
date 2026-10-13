from pydantic import BaseModel, Field
from typing import Optional, List


class WfhRequestCreate(BaseModel):
    start_date: str
    end_date: Optional[str] = None
    resuming_date: Optional[str] = None
    reason: Optional[str] = Field(None, max_length=500)
    comment: Optional[str] = Field(None, max_length=500)
    contact_number: Optional[str] = None
    approver_id: Optional[int] = None
    project_id: Optional[int] = None
    project_name: Optional[str] = None
    skip_weekends: bool = False
    skip_holidays: bool = True
    wfh_dates: Optional[List[str]] = None  # individual dates override start_date/end_date range


class WfhRequestUpdate(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    resuming_date: Optional[str] = None
    reason: Optional[str] = Field(None, max_length=500)
    comment: Optional[str] = Field(None, max_length=500)
    contact_number: Optional[str] = None
    project_id: Optional[int] = None
    project_name: Optional[str] = None


class WfhRequestReject(BaseModel):
    reason: Optional[str] = Field(None, max_length=500, description="Reason for rejection")
