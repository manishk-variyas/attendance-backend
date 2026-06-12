import logging
from datetime import date, datetime, timezone
from math import radians, sin, cos, sqrt, atan2
import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.core.database import get_db
from app.features.auth.dependencies import get_current_user, require_active
from app.features.attendance.schemas import CheckInRequest, CheckOutRequest, AttendanceResponse
from app.models.attendance import Attendance
from app.models.employee_master import EmployeeMaster
from app.models.office_location import OfficeLocation
from app.models.shift import Shift
from app.models.shift_definition import ShiftDefinition

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/attendance", tags=["attendance"])

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return r * 2 * atan2(sqrt(a), sqrt(1 - a))


def _determine_work_location(db: Session, user_lat: float, user_lng: float, location_id) -> str:
    if not location_id:
        return "WFH"
    office = db.query(OfficeLocation).filter(OfficeLocation.id == location_id).first()
    if not office:
        return "WFH"
    dist = _haversine(user_lat, user_lng, office.latitude, office.longitude)
    return "OFFICE" if dist <= office.radius_meters else "WFH"


async def _reverse_geocode(lat: float, lng: float) -> str:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                NOMINATIM_URL,
                params={"format": "json", "lat": lat, "lon": lng},
                headers={"User-Agent": "AttendanceApp/1.0"},
            )
            if resp.status_code == 200 and resp.text.strip():
                data = resp.json()
                return data.get("display_name", "")[:500]
    except Exception as e:
        logger.warning(f"Reverse geocode failed for ({lat}, {lng}): {e}")
    return ""


def _to_response(db: Session, a: Attendance) -> dict:
    data = a.to_dict()
    if a.location_id:
        office = db.query(OfficeLocation).filter(OfficeLocation.id == a.location_id).first()
        data["officeName"] = office.name if office else None
    else:
        data["officeName"] = None
    return data


@router.post("/check-in", response_model=AttendanceResponse, status_code=status.HTTP_201_CREATED)
async def check_in(
    payload: CheckInRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    keycloak_user_id = current_user["sub"]
    today = date.today()

    # Prevent duplicate check-in
    existing = db.query(Attendance).filter(
        and_(
            Attendance.keycloak_user_id == keycloak_user_id,
            Attendance.attendance_date == today,
        )
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Already checked in today")

    now = datetime.now(timezone.utc)

    # Determine shift_code and is_late
    shift_code = None
    is_late = False
    today_shift = db.query(Shift).filter(
        and_(
            Shift.keycloak_user_id == keycloak_user_id,
            Shift.date == today,
        )
    ).first()
    if today_shift:
        shift_code = today_shift.shift_code
        shift_def = db.query(ShiftDefinition).filter(
            ShiftDefinition.shift_code == shift_code
        ).first()
        if shift_def and shift_def.start_time:
            shift_start_today = datetime.combine(today, shift_def.start_time, tzinfo=now.tzinfo)
            if now > shift_start_today:
                is_late = True

    # Resolve employee's assigned office and detect work location from GPS
    emp = db.query(EmployeeMaster).filter(
        EmployeeMaster.keycloak_user_id == keycloak_user_id
    ).first()
    location_id = emp.location_id if emp else None
    work_location_status = _determine_work_location(db, payload.latitude, payload.longitude, location_id)
    check_in_location_name = await _reverse_geocode(payload.latitude, payload.longitude)

    attendance = Attendance(
        keycloak_user_id=keycloak_user_id,
        attendance_date=today,
        check_in_time=now,
        check_in_lat=payload.latitude,
        check_in_lng=payload.longitude,
        check_in_location_name=check_in_location_name,
        work_location_status=work_location_status,
        shift_code=shift_code,
        is_late=is_late,
        location_id=location_id,
    )
    db.add(attendance)
    db.commit()
    db.refresh(attendance)

    logger.info(f"Check-in: {keycloak_user_id} on {today}")
    return _to_response(db, attendance)


@router.post("/check-out", response_model=AttendanceResponse)
async def check_out(
    payload: CheckOutRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    keycloak_user_id = current_user["sub"]
    today = date.today()

    attendance = db.query(Attendance).filter(
        and_(
            Attendance.keycloak_user_id == keycloak_user_id,
            Attendance.attendance_date == today,
        )
    ).first()
    if not attendance:
        raise HTTPException(status_code=404, detail="No check-in record found for today")
    if attendance.check_out_time:
        raise HTTPException(status_code=409, detail="Already checked out today")

    attendance.check_out_time = datetime.now(timezone.utc)
    attendance.check_out_lat = payload.latitude
    attendance.check_out_lng = payload.longitude
    attendance.check_out_location_name = await _reverse_geocode(payload.latitude, payload.longitude)

    db.commit()
    db.refresh(attendance)

    logger.info(f"Check-out: {keycloak_user_id} on {today}")
    return _to_response(db, attendance)


@router.get("/today", response_model=AttendanceResponse)
async def get_today_attendance(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    keycloak_user_id = current_user["sub"]
    today = date.today()

    attendance = db.query(Attendance).filter(
        and_(
            Attendance.keycloak_user_id == keycloak_user_id,
            Attendance.attendance_date == today,
        )
    ).first()
    if not attendance:
        raise HTTPException(status_code=404, detail="No attendance record for today")
    return _to_response(db, attendance)
