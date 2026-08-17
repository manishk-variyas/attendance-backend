from datetime import datetime, timezone, timedelta, date
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.features.redmine.constants import REDMINE_TO_IANA_TZ
from app.models.employee_master import EmployeeMaster
from app.models.leave import Leave
from app.models.office_location import OfficeLocation
from app.models.shift import Shift
from app.models.shift_attendance import ShiftAttendance
from app.models.shift_definition import ShiftDefinition
from app.models.system_setting import SystemSetting


_GEOFENCE_SQL = text("""
    SELECT ST_DWithin(
        ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
        (SELECT geom FROM office_locations WHERE id = :office_id)::geography,
        :radius
    )
""")

_DISTANCE_SQL = text("""
    SELECT ROUND(ST_Distance(
        ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
        (SELECT geom FROM office_locations WHERE id = :office_id)::geography
    )::INT / 1000.0, 1) AS distance_km
""")


def _safe_zone(tz_str: str) -> ZoneInfo:
    iana = REDMINE_TO_IANA_TZ.get(tz_str, tz_str)
    try:
        return ZoneInfo(iana)
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def _shift_end_datetime(shift_date: date, start_time, end_time, tz: ZoneInfo) -> datetime:
    if start_time and end_time and end_time <= start_time:
        shift_date = shift_date + timedelta(days=1)
    return datetime.combine(shift_date, end_time, tzinfo=tz)


def _resolve_timestamp(client_timestamp: Optional[datetime]) -> datetime:
    if not client_timestamp:
        return datetime.now(timezone.utc)
    now = datetime.now(timezone.utc)
    if client_timestamp.tzinfo is None:
        client_timestamp = client_timestamp.replace(tzinfo=timezone.utc)
    if client_timestamp > now + timedelta(hours=1):
        return now
    if now - client_timestamp > timedelta(hours=24):
        return now
    return client_timestamp


def _determine_work_location(db: Session, user_lat: float, user_lng: float, location_id) -> str:
    if not location_id:
        return "WFH"
    office = db.execute(select(OfficeLocation).where(OfficeLocation.id == location_id)).scalars().first()
    if not office:
        return "WFH"
    inside = db.execute(
        _GEOFENCE_SQL,
        {"lng": user_lng, "lat": user_lat, "office_id": str(location_id), "radius": office.radius_meters},
    ).scalar()
    return "OFFICE" if inside else "WFH"


def _location_name(db: Session, lat: float, lng: float, location_id) -> str:
    if not location_id:
        return f"{lat:.4f}, {lng:.4f}"
    office = db.execute(select(OfficeLocation).where(OfficeLocation.id == location_id)).scalars().first()
    if not office:
        return f"{lat:.4f}, {lng:.4f}"
    inside = db.execute(
        _GEOFENCE_SQL,
        {"lng": lng, "lat": lat, "office_id": str(location_id), "radius": office.radius_meters},
    ).scalar()
    if inside:
        return f"{office.name}, {office.address}" if office.address else office.name
    try:
        dist = db.execute(
            _DISTANCE_SQL,
            {"lng": lng, "lat": lat, "office_id": str(location_id)},
        ).scalar()
        if dist is not None:
            return f"{dist} km from {office.name}"
    except Exception:
        pass
    return f"{lat:.4f}, {lng:.4f}"


class ShiftAttendanceService:
    """Lean service for per-shift punches stored in the `shift_attendance` table."""

    def check_in(
        self,
        db: Session,
        keycloak_user_id: str,
        shift_id: str,
        latitude: Optional[float],
        longitude: Optional[float],
        client_timestamp: Optional[datetime],
    ) -> ShiftAttendance:
        shift = db.execute(
            select(Shift).where(Shift.id == shift_id, Shift.keycloak_user_id == keycloak_user_id)
        ).scalars().first()
        if not shift:
            raise HTTPException(status_code=404, detail="Shift not found")

        sd = db.execute(
            select(ShiftDefinition).where(ShiftDefinition.shift_code == shift.shift_code)
        ).scalars().first()
        tz = _safe_zone(sd.timezone or "Asia/Kolkata") if sd else ZoneInfo("Asia/Kolkata")

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
            select(ShiftAttendance).where(ShiftAttendance.shift_id == shift.id)
        ).scalars().first()
        if existing:
            return existing

        check_in_time = _resolve_timestamp(client_timestamp)

        grace_min = 0
        company = db.execute(select(SystemSetting).where(SystemSetting.id == "company")).scalars().first()
        if company and company.grace_minutes:
            grace_min = company.grace_minutes

        start_dt = datetime.combine(shift.date, sd.start_time, tzinfo=tz) if (sd and sd.start_time) else None
        end_dt = _shift_end_datetime(shift.date, sd.start_time, sd.end_time, tz) if (sd and sd.end_time) else None

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
            work_location_status = _determine_work_location(db, latitude, longitude, location_id)
            if work_location_status == "OFFICE" and location_id:
                office = db.execute(
                    select(OfficeLocation).where(OfficeLocation.id == location_id)
                ).scalars().first()
                check_in_location_name = f"{office.name}, {office.address}" if office and office.address else (office.name if office else "")
            else:
                check_in_location_name = _location_name(db, latitude, longitude, location_id)

        punch = ShiftAttendance(
            shift_id=shift.id,
            keycloak_user_id=keycloak_user_id,
            attendance_date=shift.date,
            shift_code=shift.shift_code,
            check_in_time=check_in_time,
            check_in_lat=latitude,
            check_in_lng=longitude,
            check_in_location_name=check_in_location_name,
            work_location_status=work_location_status,
            is_late=is_late,
            status="in_progress",
        )
        db.add(punch)
        shift.status = "In Progress"
        db.commit()
        db.refresh(punch)

        return punch


shift_attendance_service = ShiftAttendanceService()
