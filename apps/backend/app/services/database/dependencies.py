from fastapi import Depends
from sqlalchemy.orm import Session
from app.core.database import get_db

from app.services.database.shift_service import ShiftService, ShiftDefinitionService
from app.services.database.leave_service import LeaveService, LeaveBalanceService
from app.services.database.holiday_service import HolidayService
from app.services.database.notification_service import NotificationService
from app.services.database.recording_service import RecordingService
from app.services.database.location_service import LocationService
from app.services.database.office_location_service import OfficeLocationService
from app.services.database.system_setting_service import SystemSettingService

from app.features.leaves.services.leave_service import LeaveBusinessService

def get_shift_service(db: Session = Depends(get_db)) -> ShiftService:
    return ShiftService(db)

def get_shift_definition_service(db: Session = Depends(get_db)) -> ShiftDefinitionService:
    return ShiftDefinitionService(db)

def get_leave_service(db: Session = Depends(get_db)) -> LeaveService:
    return LeaveService(db)

def get_leave_balance_service(db: Session = Depends(get_db)) -> LeaveBalanceService:
    return LeaveBalanceService(db)

def get_holiday_service(db: Session = Depends(get_db)) -> HolidayService:
    return HolidayService(db)

def get_notification_service(db: Session = Depends(get_db)) -> NotificationService:
    return NotificationService(db)

def get_recording_service(db: Session = Depends(get_db)) -> RecordingService:
    return RecordingService(db)

def get_location_service(db: Session = Depends(get_db)) -> LocationService:
    return LocationService(db)

def get_office_location_service(db: Session = Depends(get_db)) -> OfficeLocationService:
    return OfficeLocationService(db)

def get_system_setting_service(db: Session = Depends(get_db)) -> SystemSettingService:
    return SystemSettingService(db)

def get_leave_business_service(
    leave_db: LeaveService = Depends(get_leave_service),
    holiday_db: HolidayService = Depends(get_holiday_service),
    balance_db: LeaveBalanceService = Depends(get_leave_balance_service)
) -> LeaveBusinessService:
    return LeaveBusinessService(leave_db, holiday_db, balance_db)
