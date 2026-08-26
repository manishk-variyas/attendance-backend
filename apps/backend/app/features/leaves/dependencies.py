from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.database.leave_service import LeaveService
from app.services.database.holiday_service import HolidayService
from app.features.leaves.services.leave_service import LeaveBusinessService


def get_leave_service(db: Session = Depends(get_db)) -> LeaveService:
    return LeaveService(db)


def get_holiday_service(db: Session = Depends(get_db)) -> HolidayService:
    return HolidayService(db)


def get_leave_business_service(
    leave_db: LeaveService = Depends(get_leave_service),
    holiday_db: HolidayService = Depends(get_holiday_service),
) -> LeaveBusinessService:
    return LeaveBusinessService(leave_db, holiday_db)
