import logging
from datetime import date, datetime, timezone, timedelta
from typing import Optional
from zoneinfo import ZoneInfo
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import and_, text

from app.core.database import get_db
from app.features.auth.dependencies import get_current_user, require_active
from app.features.attendance.schemas import CheckInRequest, CheckOutRequest, AttendanceResponse, CompleteAttendanceRequest
from app.models.attendance import Attendance
from app.models.employee_master import EmployeeMaster
from app.models.office_location import OfficeLocation
from app.models.shift import Shift
from app.models.shift_definition import ShiftDefinition
from app.models.location import UserLocation
from app.models.holiday import Holiday
from app.models.leave_balance import LeaveBalance
from app.models.system_setting import SystemSetting

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/attendance", tags=["attendance"])

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
GEOFENCE_SQL = text("""
    SELECT ST_DWithin(
        ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
        (SELECT geom FROM office_locations WHERE id = :office_id)::geography,
        :radius
    )
""")

_GEO_CACHE: dict = {}


def _safe_zone(tz_str: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_str)
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def _determine_work_location(db: Session, user_lat: float, user_lng: float, location_id) -> str:
    if not location_id:
        return "WFH"
    office = db.query(OfficeLocation).filter(OfficeLocation.id == location_id).first()
    if not office:
        return "WFH"
    inside = db.execute(
        GEOFENCE_SQL,
        {"lng": user_lng, "lat": user_lat, "office_id": str(location_id), "radius": office.radius_meters},
    ).scalar()
    return "OFFICE" if inside else "WFH"


async def _reverse_geocode(lat: float, lng: float) -> str:
    cache_key = (round(lat, 3), round(lng, 3))
    if cache_key in _GEO_CACHE:
        return _GEO_CACHE[cache_key]
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                NOMINATIM_URL,
                params={"format": "json", "lat": lat, "lon": lng},
                headers={"User-Agent": "AttendanceApp/1.0"},
            )
            if resp.status_code == 200 and resp.text.strip():
                data = resp.json()
                name = data.get("display_name", "")[:500]
                if name:
                    _GEO_CACHE[cache_key] = name
                return name
    except Exception as e:
        logger.warning(f"Reverse geocode failed for ({lat}, {lng}): {e}")
    return ""


def _credit_comp_off_if_holiday(db: Session, attendance: Attendance) -> None:
    if attendance.comp_off_credited:
        return
    if not attendance.check_out_time:
        return

    holiday = db.query(Holiday).filter(Holiday.holiday_date == attendance.attendance_date).first()
    if not holiday:
        return

    keycloak_id = attendance.keycloak_user_id
    balance = db.query(LeaveBalance).filter(LeaveBalance.keycloak_user_id == keycloak_id).first()
    if balance:
        balance.accrued_compoff = (balance.accrued_compoff or 0) + 1
    else:
        db.add(LeaveBalance(
            keycloak_user_id=keycloak_id,
            year=attendance.attendance_date.year,
            total_earned=12.0,
            used_earned=0.0,
            accrued_compoff=1,
            consumed_compoff=0,
            unpaid=0,
        ))
    attendance.comp_off_credited = True
    attendance.remarks = (attendance.remarks or "") + " | Comp-off credited: " + holiday.holiday_name
    db.commit()
    logger.info(f"Comp-off credited: {keycloak_id} for {holiday.holiday_name} on {attendance.attendance_date}")


def _resolve_timestamp(client_timestamp: Optional[datetime]) -> tuple[datetime, bool]:
    if not client_timestamp:
        return datetime.now(timezone.utc), False
    now = datetime.now(timezone.utc)
    if client_timestamp.tzinfo is None:
        client_timestamp = client_timestamp.replace(tzinfo=timezone.utc)
    if client_timestamp > now + timedelta(hours=1):
        return now, False
    if now - client_timestamp > timedelta(hours=24):
        return now, False
    return client_timestamp, True


def _shift_end_datetime(shift_date: date, start_time, end_time, tz: ZoneInfo) -> datetime:
    if start_time and end_time and end_time <= start_time:
        shift_date = shift_date + timedelta(days=1)
    return datetime.combine(shift_date, end_time, tzinfo=tz)


def _to_response(db: Session, a: Attendance) -> dict:
    data = a.to_dict()
    if a.location_id:
        office = db.query(OfficeLocation).filter(OfficeLocation.id == a.location_id).first()
        data["officeName"] = office.name if office else None
    else:
        data["officeName"] = None
    if a.shift_code:
        shift_def = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == a.shift_code).first()
        data["shiftName"] = shift_def.shift_name if shift_def else None
    else:
        data["shiftName"] = None
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

    target_date = today
    if payload.date:
        try:
            target_date = date.fromisoformat(payload.date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
        if target_date > today:
            raise HTTPException(status_code=400, detail="Cannot check in for a future date.")
        if target_date < today - timedelta(days=1):
            raise HTTPException(status_code=400, detail="Can only check in for today or yesterday.")

    if target_date == today:
        open_att = db.query(Attendance).filter(
            and_(
                Attendance.keycloak_user_id == keycloak_user_id,
                Attendance.attendance_date == today - timedelta(days=1),
                Attendance.check_out_time.is_(None),
            )
        ).first()
        if open_att:
            raise HTTPException(status_code=400, detail="Close yesterday's attendance before checking in today.")

    existing = db.query(Attendance).filter(
        and_(
            Attendance.keycloak_user_id == keycloak_user_id,
            Attendance.attendance_date == target_date,
        )
    ).first()
    if existing:
        return JSONResponse(content=_to_response(db, existing), status_code=200)

    check_in_time, is_synced = _resolve_timestamp(payload.clientTimestamp)
    server_received_at = datetime.now(timezone.utc)
    now = check_in_time

    if target_date != today:
        is_synced = True

    shift_code = None
    is_late = False
    shift_start_time = None
    shift_tz = "Asia/Kolkata"

    today_shift = db.query(Shift).filter(
        and_(
            Shift.keycloak_user_id == keycloak_user_id,
            Shift.date == target_date,
        )
    ).first()
    if today_shift:
        shift_code = today_shift.shift_code
        shift_def = db.query(ShiftDefinition).filter(
            ShiftDefinition.shift_code == shift_code
        ).first()
        if shift_def:
            shift_start_time = shift_def.start_time
            shift_tz = shift_def.timezone or "Asia/Kolkata"

    company = None
    if not shift_start_time:
        company = db.query(SystemSetting).filter(SystemSetting.id == "company").first()
        if company and company.default_shift_start_time:
            shift_start_time = company.default_shift_start_time
            shift_tz = company.default_timezone or "Asia/Kolkata"

    if shift_start_time and now.tzinfo:
        grace_min = 0
        if not company:
            company = db.query(SystemSetting).filter(SystemSetting.id == "company").first()
        if company and company.grace_minutes:
            grace_min = company.grace_minutes
        shift_start_dt = datetime.combine(target_date, shift_start_time, tzinfo=_safe_zone(shift_tz))
        if now < shift_start_dt - timedelta(hours=2):
            raise HTTPException(
                status_code=400,
                detail=f"Too early to check in. Your shift starts at {shift_start_time.strftime('%H:%M')} on {target_date.isoformat()}.",
            )
        if now > shift_start_dt + timedelta(minutes=grace_min):
            is_late = True

    emp = db.query(EmployeeMaster).filter(
        EmployeeMaster.keycloak_user_id == keycloak_user_id
    ).first()
    location_id = emp.location_id if emp else None
    work_location_status = _determine_work_location(db, payload.latitude, payload.longitude, location_id)
    if work_location_status == "OFFICE" and location_id:
        office = db.query(OfficeLocation).filter(OfficeLocation.id == location_id).first()
        check_in_location_name = f"{office.name}, {office.address}" if office and office.address else (office.name if office else "")
    else:
        check_in_location_name = await _reverse_geocode(payload.latitude, payload.longitude)

    remarks = None
    if target_date != today:
        remarks = "Next-day check-in"

    attendance = Attendance(
        keycloak_user_id=keycloak_user_id,
        attendance_date=target_date,
        check_in_time=check_in_time,
        check_in_lat=payload.latitude,
        check_in_lng=payload.longitude,
        check_in_location_name=check_in_location_name,
        work_location_status=work_location_status,
        shift_code=shift_code,
        is_late=is_late,
        location_id=location_id,
        is_synced=is_synced,
        server_received_at=server_received_at,
        remarks=remarks,
    )
    db.add(attendance)
    db.commit()
    db.refresh(attendance)

    user_email = current_user.get("email")
    if user_email:
        existing_loc = db.query(UserLocation).filter(UserLocation.email == user_email).first()
        if existing_loc:
            existing_loc.latitude = payload.latitude
            existing_loc.longitude = payload.longitude
            existing_loc.location_name = check_in_location_name
            existing_loc.updated_at = now
        else:
            db.add(UserLocation(email=user_email, latitude=payload.latitude, longitude=payload.longitude, location_name=check_in_location_name, updated_at=now))
        db.commit()

    logger.info(f"Check-in: {keycloak_user_id} on {target_date}")
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

    target_date = today
    if payload.date:
        try:
            target_date = date.fromisoformat(payload.date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
        if target_date > today:
            raise HTTPException(status_code=400, detail="Cannot check out for a future date.")
        if target_date < today - timedelta(days=1):
            raise HTTPException(status_code=400, detail="Can only check out for today or yesterday.")

    attendance = db.query(Attendance).filter(
        and_(
            Attendance.keycloak_user_id == keycloak_user_id,
            Attendance.attendance_date == target_date,
        )
    ).first()
    if not attendance:
        attendance = db.query(Attendance).filter(
            and_(
                Attendance.keycloak_user_id == keycloak_user_id,
                Attendance.attendance_date == target_date - timedelta(days=1),
                Attendance.check_out_time.is_(None),
            )
        ).first()
    if not attendance:
        raise HTTPException(status_code=404, detail=f"No active check-in found for {target_date}")
    if attendance.check_out_time:
        return JSONResponse(content=_to_response(db, attendance), status_code=200)

    check_out_time, is_synced = _resolve_timestamp(payload.clientTimestamp)
    attendance.check_out_time = check_out_time
    if is_synced or target_date != today:
        attendance.is_synced = True
        if not attendance.server_received_at:
            attendance.server_received_at = datetime.now(timezone.utc)
    if target_date != today:
        attendance.remarks = (attendance.remarks or "") + (" | Next-day checkout" if attendance.remarks else "Next-day checkout")
    attendance.check_out_lat = payload.latitude
    attendance.check_out_lng = payload.longitude
    check_out_ws = _determine_work_location(db, payload.latitude, payload.longitude, attendance.location_id)
    if check_out_ws == "OFFICE" and attendance.location_id:
        office = db.query(OfficeLocation).filter(OfficeLocation.id == attendance.location_id).first()
        attendance.check_out_location_name = f"{office.name}, {office.address}" if office and office.address else (office.name if office else "")
    else:
        attendance.check_out_location_name = await _reverse_geocode(payload.latitude, payload.longitude)

    if payload.remarks:
        attendance.remarks = payload.remarks

    shift_start_time = None
    shift_end_time = None
    end_tz = "Asia/Kolkata"
    if attendance.shift_code:
        shift_def = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == attendance.shift_code).first()
        if shift_def:
            shift_start_time = shift_def.start_time
            shift_end_time = shift_def.end_time
            end_tz = shift_def.timezone or "Asia/Kolkata"
    if not shift_end_time:
        company = db.query(SystemSetting).filter(SystemSetting.id == "company").first()
        if company and company.default_shift_end_time:
            shift_end_time = company.default_shift_end_time
            end_tz = company.default_timezone or "Asia/Kolkata"
    if shift_end_time and attendance.check_out_time.tzinfo:
        shift_end_dt = _shift_end_datetime(attendance.attendance_date, shift_start_time, shift_end_time, _safe_zone(end_tz))
        if attendance.check_out_time < shift_end_dt:
            attendance.remarks = (attendance.remarks or "") + " | Early leave"

    user_email = current_user.get("email")
    if user_email:
        existing_loc = db.query(UserLocation).filter(UserLocation.email == user_email).first()
        if existing_loc:
            existing_loc.latitude = payload.latitude
            existing_loc.longitude = payload.longitude
            existing_loc.location_name = attendance.check_out_location_name
            existing_loc.updated_at = attendance.check_out_time
        else:
            db.add(UserLocation(email=user_email, latitude=payload.latitude, longitude=payload.longitude, location_name=attendance.check_out_location_name, updated_at=attendance.check_out_time))
        db.commit()

    db.commit()
    db.refresh(attendance)

    _credit_comp_off_if_holiday(db, attendance)

    logger.info(f"Check-out: {keycloak_user_id} on {today}")
    return _to_response(db, attendance)


@router.post("/complete", response_model=AttendanceResponse, status_code=status.HTTP_201_CREATED)
async def complete_attendance(
    payload: CompleteAttendanceRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    keycloak_user_id = current_user["sub"]
    today = date.today()

    try:
        target_date = date.fromisoformat(payload.date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    if target_date > today or target_date < today - timedelta(days=1):
        raise HTTPException(status_code=400, detail="Only today or yesterday allowed.")

    if payload.checkInTime >= payload.checkOutTime:
        raise HTTPException(status_code=400, detail="Check-in must be before check-out.")

    existing = db.query(Attendance).filter(
        and_(
            Attendance.keycloak_user_id == keycloak_user_id,
            Attendance.attendance_date == target_date,
        )
    ).first()

    emp = db.query(EmployeeMaster).filter(
        EmployeeMaster.keycloak_user_id == keycloak_user_id
    ).first()
    location_id = emp.location_id if emp else None

    shift_code = None
    shift = db.query(Shift).filter(
        and_(
            Shift.keycloak_user_id == keycloak_user_id,
            Shift.date == target_date,
        )
    ).first()
    if shift:
        shift_code = shift.shift_code

    ws = _determine_work_location(db, payload.latitude, payload.longitude, location_id)
    if ws == "OFFICE" and location_id:
        office = db.query(OfficeLocation).filter(OfficeLocation.id == location_id).first()
        loc_name = f"{office.name}, {office.address}" if office and office.address else (office.name if office else "")
    else:
        loc_name = await _reverse_geocode(payload.latitude, payload.longitude)

    if existing:
        existing.check_out_time = payload.checkOutTime
        existing.check_out_lat = payload.latitude
        existing.check_out_lng = payload.longitude
        existing.check_out_location_name = loc_name
        existing.is_synced = True
        if not existing.server_received_at:
            existing.server_received_at = datetime.now(timezone.utc)
        existing.remarks = (existing.remarks or "") + (" | " if existing.remarks else "") + "Next-day completion"
        if payload.remarks:
            existing.remarks += f" ({payload.remarks})"
        db.commit()
        db.refresh(existing)
        _credit_comp_off_if_holiday(db, existing)
        return _to_response(db, existing)

    attendance = Attendance(
        keycloak_user_id=keycloak_user_id,
        attendance_date=target_date,
        check_in_time=payload.checkInTime,
        check_in_lat=payload.latitude,
        check_in_lng=payload.longitude,
        check_in_location_name=loc_name,
        check_out_time=payload.checkOutTime,
        check_out_lat=payload.latitude,
        check_out_lng=payload.longitude,
        check_out_location_name=loc_name,
        work_location_status=ws,
        shift_code=shift_code,
        location_id=location_id,
        is_synced=True,
        server_received_at=datetime.now(timezone.utc),
        remarks=f"Next-day completion" + (f" ({payload.remarks})" if payload.remarks else ""),
    )
    db.add(attendance)
    db.commit()
    db.refresh(attendance)

    _credit_comp_off_if_holiday(db, attendance)

    logger.info(f"Complete attendance: {keycloak_user_id} on {target_date}")
    return _to_response(db, attendance)


@router.get("/pending")
async def get_pending_attendance(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    keycloak_user_id = current_user["sub"]
    today = date.today()
    yesterday = today - timedelta(days=1)

    unattended = db.query(Attendance).filter(
        and_(
            Attendance.keycloak_user_id == keycloak_user_id,
            Attendance.attendance_date == yesterday,
            Attendance.check_out_time.is_(None),
        )
    ).order_by(Attendance.attendance_date.asc()).first()
    if unattended:
        result = _to_response(db, unattended)
        result["pendingType"] = "forgot_end"
        result["shiftStartTime"] = None
        result["shiftEndTime"] = None
        if unattended.shift_code:
            shift_def = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == unattended.shift_code).first()
            if shift_def:
                tz = _safe_zone(shift_def.timezone or "Asia/Kolkata")
                if shift_def.start_time:
                    result["shiftStartTime"] = datetime.combine(unattended.attendance_date, shift_def.start_time, tzinfo=tz).isoformat()
                if shift_def.end_time:
                    result["shiftEndTime"] = _shift_end_datetime(unattended.attendance_date, shift_def.start_time, shift_def.end_time, tz).isoformat()
        return result

    past_shift = db.query(Shift).filter(
        and_(
            Shift.keycloak_user_id == keycloak_user_id,
            Shift.date == yesterday,
        )
    ).first()
    if past_shift:
        past_att = db.query(Attendance).filter(
            and_(
                Attendance.keycloak_user_id == keycloak_user_id,
                Attendance.attendance_date == yesterday,
            )
        ).first()
        if not past_att:
            return {
                "pendingType": "forgot_start",
                "attendanceDate": yesterday.isoformat(),
                "shiftCode": past_shift.shift_code,
                "shiftName": None,
                "shiftStartTime": None,
                "shiftEndTime": None,
            }

    raise HTTPException(status_code=404, detail="No pending attendance action")


@router.get("/today")
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
        attendance = db.query(Attendance).filter(
            and_(
                Attendance.keycloak_user_id == keycloak_user_id,
                Attendance.attendance_date == today - timedelta(days=1),
                Attendance.check_out_time.is_(None),
            )
        ).first()
    if not attendance:
        raise HTTPException(status_code=404, detail="No attendance record for today")

    result = _to_response(db, attendance)

    shift_start_dt = None
    shift_end_dt = None
    if attendance.shift_code:
        shift_def = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == attendance.shift_code).first()
        if shift_def:
            tz = _safe_zone(shift_def.timezone or "Asia/Kolkata")
            if shift_def.start_time:
                shift_start_dt = datetime.combine(today, shift_def.start_time, tzinfo=tz)
            if shift_def.end_time:
                shift_end_dt = _shift_end_datetime(today, shift_def.start_time, shift_def.end_time, tz)

    result["shiftStartTime"] = shift_start_dt.isoformat() if shift_start_dt else None
    result["shiftEndTime"] = shift_end_dt.isoformat() if shift_end_dt else None
    if shift_end_dt:
        result["remainingHours"] = round(max((shift_end_dt - datetime.now(timezone.utc)).total_seconds() / 3600, 0), 2)
    else:
        result["remainingHours"] = None
    return result


@router.get("/history")
async def get_attendance_history(
    from_date: str = Query(..., description="Start date (YYYY-MM-DD)"),
    to_date: str = Query(..., description="End date (YYYY-MM-DD)"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    keycloak_user_id = current_user["sub"]
    try:
        f = date.fromisoformat(from_date)
        t = date.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    query = db.query(Attendance).filter(
        and_(
            Attendance.keycloak_user_id == keycloak_user_id,
            Attendance.attendance_date >= f,
            Attendance.attendance_date <= t,
        )
    )
    total = query.count()
    offset = (page - 1) * per_page
    records = query.order_by(Attendance.attendance_date.desc()).offset(offset).limit(per_page).all()

    emp = db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id == keycloak_user_id).first()
    user_name = f"{emp.first_name} {emp.middle_name or ''} {emp.last_name}".strip().replace("  ", " ") if emp else None

    records_data = []
    for r in records:
        rec = _to_response(db, r)
        rec["userEmail"] = current_user.get("email")
        rec["userName"] = user_name
        if r.shift_code:
            sd = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == r.shift_code).first()
            if sd:
                tz = _safe_zone(sd.timezone or "Asia/Kolkata")
                if sd.start_time:
                    rec["shiftStartTime"] = datetime.combine(r.attendance_date, sd.start_time, tzinfo=tz).isoformat()
                if sd.end_time:
                    rec["shiftEndTime"] = _shift_end_datetime(r.attendance_date, sd.start_time, sd.end_time, tz).isoformat()
        records_data.append(rec)

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
        "records": records_data,
    }
