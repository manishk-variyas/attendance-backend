from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.employee_master import EmployeeMaster
from app.models.leave import Leave
from app.models.office_location import OfficeLocation
from app.models.shift import Shift
from app.models.attendance_punch import AttendancePunch
from app.models.shift_definition import ShiftDefinition
from app.models.system_setting import SystemSetting
from app.utils.shift_time import safe_zone, shift_end_datetime, resolve_timestamp_dt
from app.features.attendance.services.geo import determine_work_location, location_name, find_office_at
from app.features.attendance.services.punch_service import recompute_daily_attendance


class ShiftAttendanceService:
    """Per-shift punches stored in `attendance_punches` (multi-shift source of truth).

    After every punch write the daily `attendance` rollup is recomputed so all
    existing readers keep working unchanged.
    """

    def check_in(
        self,
        db: Session,
        keycloak_user_id: str,
        shift_id: str,
        latitude: Optional[float],
        longitude: Optional[float],
        client_timestamp: Optional[datetime],
        check_in_device: Optional[str] = None,
    ) -> AttendancePunch:
        shift = db.execute(
            select(Shift).where(Shift.id == shift_id, Shift.keycloak_user_id == keycloak_user_id)
        ).scalars().first()
        if not shift:
            raise HTTPException(status_code=404, detail="Shift not found")

        sd = db.execute(
            select(ShiftDefinition).where(ShiftDefinition.shift_code == shift.shift_code)
        ).scalars().first()
        tz = safe_zone(sd.timezone or "Asia/Kolkata") if sd else ZoneInfo("Asia/Kolkata")

        on_leave = db.execute(
            select(Leave).where(
                Leave.keycloak_user_id == keycloak_user_id,
                Leave.approval_status.in_(["approved", "emergency"]),
                Leave.start_date <= shift.date,
                Leave.end_date >= shift.date,
            )
        ).scalars().first()
        if on_leave:
            raise HTTPException(status_code=400, detail="You are on leave for this shift date. Cannot check in.")

        existing = db.execute(
            select(AttendancePunch).where(AttendancePunch.shift_id == shift.id)
        ).scalars().first()
        if existing:
            return existing

        check_in_time = resolve_timestamp_dt(client_timestamp)

        grace_min = 0
        company = db.execute(select(SystemSetting).where(SystemSetting.id == "company")).scalars().first()
        if company and company.grace_minutes:
            grace_min = company.grace_minutes

        start_dt = datetime.combine(shift.date, sd.start_time, tzinfo=tz) if (sd and sd.start_time) else None
        end_dt = shift_end_datetime(shift.date, sd.start_time, sd.end_time, tz) if (sd and sd.end_time) else None

        is_late = False
        if start_dt:
            if check_in_time < start_dt - timedelta(hours=1):
                raise HTTPException(
                    status_code=400,
                    detail=f"Too early to check in. Shift starts at {sd.start_time.strftime('%H:%M')}.",
                )
            if check_in_time > start_dt + timedelta(minutes=grace_min):
                is_late = True
        if end_dt and check_in_time > end_dt + timedelta(hours=2):
            raise HTTPException(status_code=400, detail="Shift has already ended. Cannot check in.")

        emp = db.execute(
            select(EmployeeMaster).where(EmployeeMaster.keycloak_user_id == keycloak_user_id)
        ).scalars().first()
        location_id = emp.location_id if emp else None
        if shift.location_id:
            location_id = shift.location_id

        if shift.work_location_status == "WFH":
            work_location_status = "WFH"
            check_in_location_name = "Home"
        else:
            work_location_status = determine_work_location(db, latitude, longitude, location_id)
            if work_location_status == "OFFICE" and location_id:
                office = db.execute(
                    select(OfficeLocation).where(OfficeLocation.id == location_id)
                ).scalars().first()
                check_in_location_name = f"{office.name}, {office.address}" if office and office.address else (office.name if office else "")
            else:
                check_in_location_name = location_name(db, latitude, longitude, location_id)

        punch = AttendancePunch(
            shift_id=shift.id,
            keycloak_user_id=keycloak_user_id,
            attendance_date=shift.date,
            shift_code=shift.shift_code,
            check_in_time=check_in_time,
            check_in_lat=latitude,
            check_in_lng=longitude,
            check_in_location_name=check_in_location_name,
            check_in_location_id=find_office_at(db, latitude, longitude),
            check_in_device=check_in_device,
            work_location_status=work_location_status,
            is_late=is_late,
            status="in_progress",
        )
        db.add(punch)
        shift.status = "In Progress"
        db.flush()
        recompute_daily_attendance(db, keycloak_user_id, shift.date)
        db.commit()
        db.refresh(punch)

        return punch

    def check_out(
        self,
        db: Session,
        keycloak_user_id: str,
        shift_id: str,
        latitude: Optional[float],
        longitude: Optional[float],
        client_timestamp: Optional[datetime],
        remarks: Optional[str] = None,
        check_out_device: Optional[str] = None,
    ) -> AttendancePunch:
        punch = db.execute(
            select(AttendancePunch).where(
                AttendancePunch.shift_id == shift_id,
                AttendancePunch.keycloak_user_id == keycloak_user_id,
            )
        ).scalars().first()
        if not punch:
            raise HTTPException(status_code=404, detail="No check-in found for this shift")

        if punch.check_out_time:
            return punch

        shift = db.execute(
            select(Shift).where(Shift.id == shift_id)
        ).scalars().first()
        if not shift:
            raise HTTPException(status_code=404, detail="Shift not found")

        check_out_time = resolve_timestamp_dt(client_timestamp)

        emp = db.execute(
            select(EmployeeMaster).where(EmployeeMaster.keycloak_user_id == keycloak_user_id)
        ).scalars().first()
        location_id = emp.location_id if emp else None
        if shift.location_id:
            location_id = shift.location_id

        if shift.work_location_status == "WFH":
            work_location_status = "WFH"
            check_out_location_name = "Home"
        else:
            work_location_status = determine_work_location(db, latitude, longitude, location_id)
            if work_location_status == "OFFICE" and location_id:
                office = db.execute(
                    select(OfficeLocation).where(OfficeLocation.id == location_id)
                ).scalars().first()
                check_out_location_name = f"{office.name}, {office.address}" if office and office.address else (office.name if office else "")
            else:
                check_out_location_name = location_name(db, latitude, longitude, location_id)

        punch.check_out_time = check_out_time
        punch.check_out_lat = latitude
        punch.check_out_lng = longitude
        punch.check_out_location_name = check_out_location_name
        punch.check_out_location_id = find_office_at(db, latitude, longitude)
        punch.check_out_device = check_out_device
        punch.work_location_status = work_location_status
        punch.status = "completed"
        if remarks:
            punch.remarks = remarks

        if punch.check_in_time:
            delta = check_out_time - punch.check_in_time
            if delta.total_seconds() > 0:
                punch.total_hours = round(delta.total_seconds() / 3600.0, 2)

        shift.status = "Completed"
        db.flush()
        recompute_daily_attendance(db, keycloak_user_id, punch.attendance_date)
        db.commit()
        db.refresh(punch)

        return punch


shift_attendance_service = ShiftAttendanceService()
