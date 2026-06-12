from pydantic import BaseModel, Field
from typing import Optional


class CheckInRequest(BaseModel):
    latitude: float
    longitude: float


class CheckOutRequest(BaseModel):
    latitude: float
    longitude: float


class AttendanceResponse(BaseModel):
    id: str
    keycloakUserId: str
    attendanceDate: str
    checkInTime: Optional[str] = None
    checkInLat: Optional[float] = None
    checkInLng: Optional[float] = None
    checkInLocationName: Optional[str] = None
    checkOutTime: Optional[str] = None
    checkOutLat: Optional[float] = None
    checkOutLng: Optional[float] = None
    checkOutLocationName: Optional[str] = None
    locationId: Optional[str] = None
    officeName: Optional[str] = None
    workLocationStatus: str
    shiftCode: Optional[str] = None
    totalHours: Optional[float] = None
    isLate: bool
    status: str
    remarks: Optional[str] = None
    createdAt: str
    updatedAt: str

    class Config:
        from_attributes = True


class AdminAttendanceCreate(BaseModel):
    user_email: str
    attendance_date: str
    check_in_time: Optional[str] = None
    check_out_time: Optional[str] = None
    work_location_status: str = "OFFICE"
    shift_code: Optional[str] = None
    status: str = "present"
    is_late: bool = False
    remarks: Optional[str] = None


class AdminAttendanceUpdate(BaseModel):
    check_in_time: Optional[str] = None
    check_out_time: Optional[str] = None
    work_location_status: Optional[str] = None
    shift_code: Optional[str] = None
    status: Optional[str] = None
    is_late: Optional[bool] = None
    remarks: Optional[str] = None
