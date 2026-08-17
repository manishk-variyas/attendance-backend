from fastapi import Depends
from sqlalchemy.orm import Session
from app.core.database import get_db

from app.services.database.shift_service import ShiftService, ShiftDefinitionService
from app.services.database.leave_service import LeaveService
from app.services.database.holiday_service import HolidayService
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

def get_holiday_service(db: Session = Depends(get_db)) -> HolidayService:
    return HolidayService(db)

def get_location_service(db: Session = Depends(get_db)) -> LocationService:
    return LocationService(db)

def get_office_location_service(db: Session = Depends(get_db)) -> OfficeLocationService:
    return OfficeLocationService(db)

def get_system_setting_service(db: Session = Depends(get_db)) -> SystemSettingService:
    return SystemSettingService(db)

def get_leave_business_service(
    leave_db: LeaveService = Depends(get_leave_service),
    holiday_db: HolidayService = Depends(get_holiday_service),
) -> LeaveBusinessService:
    return LeaveBusinessService(leave_db, holiday_db)
