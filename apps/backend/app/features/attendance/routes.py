import logging
from datetime import date, datetime, timezone, timedelta
from typing import Optional
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, text

from app.core.database import get_db
from app.features.auth.dependencies import get_current_user, require_active
from app.features.attendance.schemas import CheckInRequest, CheckOutRequest, AttendanceResponse, CompleteAttendanceRequest, TodayAttendanceResponse
from app.models.attendance import Attendance
from app.models.employee_master import EmployeeMaster
from app.models.office_location import OfficeLocation
from app.models.shift import Shift
from app.models.shift_definition import ShiftDefinition
from app.models.location import UserLocation
from app.models.holiday import Holiday
from app.models.leave_balance import LeaveBalance
from app.models.leave import Leave
from app.features.redmine.constants import REDMINE_TO_IANA_TZ
from app.features.redmine.sql_service import RedmineSQLService
from app.models.system_setting import SystemSetting

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/attendance", tags=["attendance"])

GEOFENCE_SQL = text("""
    SELECT ST_DWithin(
        ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
        (SELECT geom FROM office_locations WHERE id = :office_id)::geography,
        :radius
    )
""")

DISTANCE_SQL = text("""
    SELECT ROUND(ST_Distance(
        ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
        (SELECT geom FROM office_locations WHERE id = :office_id)::geography
    )::INT / 1000.0, 1) AS distance_km
""")


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


def _safe_zone(tz_str: str) -> ZoneInfo:
    iana = REDMINE_TO_IANA_TZ.get(tz_str, tz_str)
    try:
        return ZoneInfo(iana)
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def _location_name(db: Session, lat: float, lng: float, location_id) -> str:
    if not location_id:
        return f"{lat:.4f}, {lng:.4f}"
    office = db.query(OfficeLocation).filter(OfficeLocation.id == location_id).first()
    if not office:
        return f"{lat:.4f}, {lng:.4f}"
    inside = db.execute(
        GEOFENCE_SQL,
        {"lng": lng, "lat": lat, "office_id": str(location_id), "radius": office.radius_meters},
    ).scalar()
    if inside:
        return f"{office.name}, {office.address}" if office.address else office.name
    try:
        dist = db.execute(
            DISTANCE_SQL,
            {"lng": lng, "lat": lat, "office_id": str(location_id)},
        ).scalar()
        if dist is not None:
            return f"{dist} km from {office.name}"
    except Exception:
        pass
    return f"{lat:.4f}, {lng:.4f}"


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


def _find_matching_shift(db: Session, keycloak_user_id: str, shift_code: str,
                         check_in_time: datetime) -> Optional[Shift]:
    """Return the shift whose active window contains check_in_time.
    Uses shift_master.timezone for timezone-aware window computation.
    Falls back to the latest shift by date if no window matches."""
    sd = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == shift_code).first()
    if not sd:
        return None
    candidates = db.query(Shift).filter(
        Shift.keycloak_user_id == keycloak_user_id,
        Shift.shift_code == shift_code,
    ).all()
    tz = _safe_zone(sd.timezone or "Asia/Kolkata")
    for c in candidates:
        win_start = datetime.combine(c.date, sd.start_time, tzinfo=tz)
        win_end = _shift_end_datetime(c.date, sd.start_time, sd.end_time, tz)
        if win_start <= check_in_time <= win_end:
            return c
    if candidates:
        candidates.sort(key=lambda x: x.date)
        return candidates[-1]
    return None


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
    data["perDiemEligible"] = a.per_diem_eligible
    data["conveyanceEligible"] = a.conveyance_eligible
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

    on_leave = db.query(Leave).filter(
        and_(
            Leave.keycloak_user_id == keycloak_user_id,
            Leave.approval_status.in_(["approved", "emergency"]),
            Leave.start_date <= target_date,
            Leave.end_date >= target_date,
        )
    ).first()
    if on_leave:
        raise HTTPException(status_code=400, detail="You are on leave today. Cannot check in.")

    check_in_time, is_synced = _resolve_timestamp(payload.clientTimestamp)
    server_received_at = datetime.now(timezone.utc)
    now = check_in_time

    if target_date != today:
        is_synced = True

    shift_code = None
    is_late = False
    shift_start_time = None
    shift_tz = "Asia/Kolkata"

    # Widen date range so overnight / timezone-crossing shifts are visible
    # when the server runs on UTC (same fix as /today endpoint).
    q_start = target_date - timedelta(days=1)
    q_end = target_date + timedelta(days=1)

    all_shifts_today = db.query(Shift).filter(
        and_(
            Shift.keycloak_user_id == keycloak_user_id,
            or_(
                Shift.date.in_([q_start, target_date, q_end]),
                and_(Shift.end_date.is_not(None), Shift.end_date >= q_start),
            ),
        )
    ).all()

    all_shifts_today.sort(key=lambda s: 0 if s.date == target_date else 1)

    if all_shifts_today:
        shift_defs = {
            s.shift_code: db.query(ShiftDefinition).filter(
                ShiftDefinition.shift_code == s.shift_code
            ).first()
            for s in all_shifts_today
        }

        now_tz = now.astimezone(timezone.utc)
        active_shift = None
        next_upcoming = None
        earliest = None
        earliest_dt = None

        for s in all_shifts_today:
            sd = shift_defs.get(s.shift_code)
            if not sd:
                continue
            tz = _safe_zone(sd.timezone or "Asia/Kolkata")
            # Use the shift's own calendar date — same timezone-aware fix as _pick_shift
            start_dt = datetime.combine(s.date, sd.start_time, tzinfo=tz)
            end_dt = _shift_end_datetime(s.date, sd.start_time, sd.end_time, tz)

            if earliest_dt is None or start_dt < earliest_dt:
                earliest_dt = start_dt
                earliest = (s, sd, tz, start_dt, end_dt)

            if start_dt <= now_tz <= end_dt:
                active_shift = (s, sd, tz, start_dt, end_dt)
                break

            if next_upcoming is None and start_dt > now_tz:
                next_upcoming = (s, sd, tz, start_dt, end_dt)

        picked = active_shift or next_upcoming
        if not picked:
            raise HTTPException(
                status_code=400,
                detail="All shifts for today have ended. No active or upcoming shift.",
            )
        s, sd, tz, start_dt, end_dt = picked
        shift_code = s.shift_code
        shift_start_time = sd.start_time
        shift_tz = sd.timezone or "Asia/Kolkata"

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
        if now < shift_start_dt - timedelta(hours=1):
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
    if shift_code and s:
        shift_loc = s.location_id
        if shift_loc:
            location_id = shift_loc

    is_wfh_shift = shift_code and s and s.work_location_status == "WFH"

    if is_wfh_shift:
        work_location_status = "WFH"
        check_in_location_name = "Home"
    else:
        work_location_status = _determine_work_location(db, payload.latitude, payload.longitude, location_id)
        if work_location_status == "OFFICE" and location_id:
            office = db.query(OfficeLocation).filter(OfficeLocation.id == location_id).first()
            check_in_location_name = f"{office.name}, {office.address}" if office and office.address else (office.name if office else "")
        else:
            check_in_location_name = _location_name(db, payload.latitude, payload.longitude, location_id)

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

    if shift_code and s:
        s.status = "In Progress"
        db.commit()

    user_email = current_user.get("email")
    if user_email and payload.latitude is not None and payload.longitude is not None:
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
        attendance.check_out_location_name = _location_name(db, payload.latitude, payload.longitude, attendance.location_id)

    if payload.remarks:
        attendance.remarks = payload.remarks

    # Determine effective shift date (timezone-aware) for early-leave check.
    # Look up the matching shift first so we use shift.date (e.g. July 22 IST)
    # instead of att.attendance_date (UTC July 21) for the end-time comparison.
    eff_date = attendance.attendance_date
    if attendance.shift_code and attendance.keycloak_user_id:
        shift_rec = _find_matching_shift(
            db, attendance.keycloak_user_id, attendance.shift_code, attendance.check_in_time
        )
        if shift_rec:
            eff_date = shift_rec.date
            shift_rec.status = "Ended"

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
        shift_end_dt = _shift_end_datetime(eff_date, shift_start_time, shift_end_time, _safe_zone(end_tz))
        if attendance.check_out_time < shift_end_dt:
            attendance.remarks = (attendance.remarks or "") + " | Early leave"

    user_email = current_user.get("email")
    if user_email and payload.latitude is not None and payload.longitude is not None:
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
            or_(
                Shift.date == target_date,
                and_(Shift.end_date.is_not(None), Shift.end_date >= target_date),
            ),
        )
    ).first()
    if shift:
        shift_code = shift.shift_code

    is_wfh = shift and shift.work_location_status == "WFH"
    if is_wfh:
        ws = "WFH"
        loc_name = "Home"
    else:
        ws = _determine_work_location(db, payload.latitude, payload.longitude, location_id)
        if ws == "OFFICE" and location_id:
            office = db.query(OfficeLocation).filter(OfficeLocation.id == location_id).first()
            loc_name = f"{office.name}, {office.address}" if office and office.address else (office.name if office else "")
        else:
            loc_name = _location_name(db, payload.latitude, payload.longitude, location_id)

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
        if shift:
            shift.status = "Ended"
            db.commit()
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

    if shift:
        shift.status = "Ended"
        db.commit()

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


@router.get("/today", response_model=TodayAttendanceResponse)
async def get_today_attendance(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    keycloak_user_id = current_user["sub"]
    now = datetime.now(timezone.utc)
    today = date.today()
    yesterday = today - timedelta(days=1)

    # 1. Open (unchecked-out) session takes priority
    open_att = db.query(Attendance).filter(
        and_(
            Attendance.keycloak_user_id == keycloak_user_id,
            Attendance.check_out_time.is_(None),
        )
    ).order_by(Attendance.attendance_date.desc()).first()

    if open_att:
        s = "overnight" if open_att.attendance_date < today else "in_progress"
        return _build_status_response(db, open_att, today, s)

    # 1b. Check if user is on leave today (approved or emergency)
    on_leave_today = db.query(Leave).filter(
        Leave.keycloak_user_id == keycloak_user_id,
        Leave.approval_status.in_(["approved", "emergency"]),
        Leave.start_date <= today,
        Leave.end_date >= today,
    ).first()

    # 2. Find shifts covering today (±1 calendar day to handle any timezone).
    #    A night shift at 00:00 IST on "July 22" has UTC start ~July 21 18:30.
    #    Querying only date==today(UTC) would miss it.  Widening by one day on
    #    each side lets _pick_shift (which uses the shift's own timezone from
    #    shift_master.timezone) correctly decide which shift is active right now.
    tomorrow = today + timedelta(days=1)
    shifts_today = db.query(Shift).filter(
        and_(
            Shift.keycloak_user_id == keycloak_user_id,
            or_(
                Shift.date.in_([yesterday, today, tomorrow]),
                and_(Shift.end_date.is_not(None), Shift.end_date >= yesterday),
            ),
        )
    ).order_by(Shift.date.asc()).all()

    # 3. Pick active or next-upcoming shift (same logic as check-in)
    picked = _pick_shift(db, shifts_today, today, now)

    if picked:
        shift, sd, tz, start_dt, end_dt = picked
        if shift.date > tomorrow:
            return {"status": "no_shift", "shift": None, "attendance": None, "remainingHours": None}
        # Attendance may be stored with a UTC date that differs from shift.date
        # when the shift crosses the UTC midnight boundary (e.g. 00-07 IST on
        # July 22 has check-in at UTC July 21).  Check ±1 day around shift.date.
        date_before = shift.date - timedelta(days=1)
        date_after  = shift.date + timedelta(days=1)
        shift_att = db.query(Attendance).filter(
            and_(
                Attendance.keycloak_user_id == keycloak_user_id,
                Attendance.attendance_date.in_([date_before, shift.date, date_after]),
            )
        ).first()

        if shift_att:
            s = "completed" if shift_att.check_out_time else "in_progress"
            return _build_status_response(db, shift_att, today, s)

        if on_leave_today or shift.work_location_status == "LEAVE":
            return _no_attendance_response("on_leave", shift, sd, tz, today)

        if end_dt and now > end_dt:
            return _no_attendance_response("missed", shift, sd, tz, today)

        return _no_attendance_response("yet_to_start", shift, sd, tz, today)

    # 4. Any attendance record today (checked in without a shift)
    att_today = db.query(Attendance).filter(
        and_(
            Attendance.keycloak_user_id == keycloak_user_id,
            Attendance.attendance_date == today,
        )
    ).first()

    if att_today:
        s = "completed" if att_today.check_out_time else "in_progress"
        return _build_status_response(db, att_today, today, s)

    # 5. Nothing at all
    if on_leave_today:
        return {"status": "on_leave", "shift": None, "attendance": None, "remainingHours": None}
    return {"status": "no_shift", "shift": None, "attendance": None, "remainingHours": None}


def _pick_shift(db: Session, shifts: list, target_date: date, now: datetime) -> Optional[tuple]:
    shift_defs = {
        s.shift_code: db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == s.shift_code).first()
        for s in shifts
    }
    active = None
    next_upcoming = None
    earliest = None
    earliest_dt = None

    for s in shifts:
        sd = shift_defs.get(s.shift_code)
        if not sd:
            continue
        tz = _safe_zone(sd.timezone or "Asia/Kolkata")
        # Use the shift's own calendar date (e.g. July 22 IST) so overnight
        # shifts that cross the UTC date boundary produce correct start/end
        # datetimes rather than being pinned to a UTC "today" they don't belong to.
        start_dt = datetime.combine(s.date, sd.start_time, tzinfo=tz)
        end_dt = _shift_end_datetime(s.date, sd.start_time, sd.end_time, tz)

        if earliest_dt is None or start_dt < earliest_dt:
            earliest_dt = start_dt
            earliest = (s, sd, tz, start_dt, end_dt)

        if start_dt <= now <= end_dt:
            active = (s, sd, tz, start_dt, end_dt)
            break

        if next_upcoming is None and start_dt > now:
            next_upcoming = (s, sd, tz, start_dt, end_dt)

    return active or next_upcoming or earliest


def _build_status_response(db: Session, att: Attendance, today: date, status: str) -> dict:
    shift_info = None
    shift_start_dt = None
    shift_end_dt = None
    sd = None
    if att.shift_code:
        sd = db.query(ShiftDefinition).filter(ShiftDefinition.shift_code == att.shift_code).first()
        if sd:
            # Use the shared helper to find which shift's window contains the
            # check-in time — handles night shifts crossing UTC midnight correctly.
            srec = _find_matching_shift(
                db, att.keycloak_user_id, att.shift_code, att.check_in_time
            )
            effective_date = srec.date if srec else att.attendance_date

            tz = _safe_zone(sd.timezone or "Asia/Kolkata")
            if sd.start_time:
                shift_start_dt = datetime.combine(effective_date, sd.start_time, tzinfo=tz)
            if sd.end_time:
                shift_end_dt = _shift_end_datetime(effective_date, sd.start_time, sd.end_time, tz)
            shift_info = {
                "shift_code": att.shift_code,
                "shift_name": sd.shift_name,
                "date": effective_date.isoformat(),
                "work_location_status": att.work_location_status,
                "start_time": shift_start_dt.isoformat() if shift_start_dt else None,
                "end_time": shift_end_dt.isoformat() if shift_end_dt else None,
            }
            if srec:
                shift_info["project_id"] = srec.project_id
                shift_info["project_name"] = srec.project_name

    rem_tz = _safe_zone(sd.timezone or "Asia/Kolkata") if sd else ZoneInfo("Asia/Kolkata")
    rem_end = None
    if sd and sd.end_time:
        rem_end = _shift_end_datetime(att.attendance_date, sd.start_time, sd.end_time, rem_tz)
    remaining = 0.0
    if att.check_out_time:
        remaining = 0.0
    elif rem_end:
        remaining = round(max((rem_end - datetime.now(timezone.utc)).total_seconds() / 3600, 0), 2)
    else:
        remaining = None

    return {
        "status": status,
        "shift": shift_info,
        "attendance": _to_response(db, att),
        "remainingHours": remaining,
    }


def _no_attendance_response(status: str, shift: Shift, sd: ShiftDefinition, tz: ZoneInfo, target_date: date) -> dict:
    shift_info = None
    if sd:
        # Use the shift's own calendar date so overnight / timezone-crossing
        # shifts show their correct date instead of the UTC "today" that was
        # used to widen the query window.
        effective_date = shift.date
        shift_info = {
            "shift_code": shift.shift_code,
            "shift_name": sd.shift_name,
            "date": effective_date.isoformat(),
            "work_location_status": shift.work_location_status,
            "start_time": datetime.combine(effective_date, sd.start_time, tzinfo=tz).isoformat() if sd.start_time else None,
            "end_time": _shift_end_datetime(effective_date, sd.start_time, sd.end_time, tz).isoformat() if sd.end_time else None,
            "project_id": shift.project_id,
            "project_name": shift.project_name,
        }
    return {
        "status": status,
        "shift": shift_info,
        "attendance": None,
        "remainingHours": 0.0 if status == "missed" else None,
    }


@router.get("/history")
async def get_attendance_history(
    from_date: str = Query(..., description="Start date (YYYY-MM-DD)"),
    to_date: str = Query(..., description="End date (YYYY-MM-DD)"),
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

    records = db.query(Attendance).filter(
        and_(
            Attendance.keycloak_user_id == keycloak_user_id,
            Attendance.attendance_date >= f,
            Attendance.attendance_date <= t,
        )
    ).order_by(Attendance.attendance_date.desc()).all()

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

    return records_data


@router.get("/project/{project_id}")
async def get_project_attendance(
    project_id: int,
    from_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    roles = current_user.get("roles", [])
    if "Admin" not in roles and "Project Manager" not in roles and "Project Coordinator" not in roles:
        raise HTTPException(status_code=403, detail="Admin, PM, or PC access required.")

    from app.features.redmine.sql_service import RedmineSQLService
    sql = RedmineSQLService(db)

    if "Admin" not in roles:
        email = current_user.get("email")
        rm_user = sql.get_user_by_email(email)
        if not rm_user:
            raise HTTPException(status_code=403, detail="Not found in Redmine.")
        my_projects = sql.get_projects_for_user(rm_user["id"])
        if not any(p.id == project_id for p in my_projects):
            raise HTTPException(status_code=403, detail="You are not a member of this project.")

    members = sql.get_project_members(project_id)
    if not members:
        return {"total": 0, "records": []}

    member_rm_ids = [m["user_id"] for m in members if m.get("user_id")]
    emps = db.query(EmployeeMaster).filter(EmployeeMaster.redmine_user_id.in_(member_rm_ids)).all()
    visible_ids = [e.keycloak_user_id for e in emps if e.keycloak_user_id]
    if not visible_ids:
        return {"total": 0, "records": []}

    query = db.query(Attendance).filter(
        Attendance.keycloak_user_id.in_(visible_ids)
    )
    if from_date:
        try:
            query = query.filter(Attendance.attendance_date >= date.fromisoformat(from_date))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid from_date format.")
    if to_date:
        try:
            query = query.filter(Attendance.attendance_date <= date.fromisoformat(to_date))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid to_date format.")

    records = query.order_by(Attendance.attendance_date.desc()).all()

    result = []
    for a in records:
        shift = db.query(Shift).filter(
            and_(Shift.keycloak_user_id == a.keycloak_user_id, Shift.date == a.attendance_date)
        ).first()
        if not shift or shift.project_id != project_id:
            continue

        rec = _to_response(db, a)
        emp = db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id == a.keycloak_user_id).first()
        if emp:
            rec["userEmail"] = emp.user_email
            rec["userName"] = f"{emp.first_name} {emp.last_name}".strip()
            rec["userDesignation"] = emp.designation

        rec["derivedStatus"] = "shift_ended" if a.check_out_time else "in_progress"
        rec["isAtAssignedLocation"] = a.location_id is not None and a.work_location_status == "OFFICE"
        rec["dayName"] = a.attendance_date.strftime("%A") if a.attendance_date else None

        rec["shiftProject"] = shift.project_name if shift else None

        leave = db.query(Leave).filter(
            and_(
                Leave.keycloak_user_id == a.keycloak_user_id,
                Leave.approval_status == "approved",
                Leave.start_date <= a.attendance_date,
                Leave.end_date >= a.attendance_date,
            )
        ).first()
        rec["onLeave"] = leave is not None
        rec["leaveType"] = leave.leave_type if leave else None
        rec["leaveStartDate"] = leave.start_date.isoformat() if leave and leave.start_date else None
        rec["leaveEndDate"] = leave.end_date.isoformat() if leave and leave.end_date else None
        rec["leaveStartDay"] = leave.start_date.strftime("%A") if leave and leave.start_date else None
        rec["leaveEndDay"] = leave.end_date.strftime("%A") if leave and leave.end_date else None

        if a.check_out_time:
            if a.modified_by:
                ended_by_emp = db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id == a.modified_by).first()
                rec["endedBy"] = f"{ended_by_emp.first_name} {ended_by_emp.last_name}".strip() if ended_by_emp else a.modified_by
                rec["endedByRole"] = ended_by_emp.designation if ended_by_emp else None
            elif emp:
                rec["endedBy"] = f"{emp.first_name} {emp.last_name}".strip()
                rec["endedByRole"] = emp.designation
            else:
                rec["endedBy"] = None
                rec["endedByRole"] = None
        else:
            rec["endedBy"] = None
            rec["endedByRole"] = None

        result.append(rec)

    return {
        "total": len(result),
        "records": result,
    }


@router.get("/my-projects")
async def get_my_projects_attendance(
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _: None = Depends(require_active),
):
    roles = current_user.get("roles", [])
    if "Admin" not in roles and "Project Manager" not in roles and "Project Coordinator" not in roles:
        raise HTTPException(status_code=403, detail="Admin, PM, or PC access required.")

    email = current_user.get("email")

    from app.features.redmine.sql_service import RedmineSQLService
    sql = RedmineSQLService(db)

    rm_user = sql.get_user_by_email(email)
    if not rm_user:
        raise HTTPException(status_code=403, detail="Not found in Redmine.")

    member_rm_ids = sql.get_team_member_ids(rm_user["id"])
    member_rm_ids.add(rm_user["id"])
    if not member_rm_ids:
        return {"total": 0, "records": []}

    emps = db.query(EmployeeMaster).filter(EmployeeMaster.redmine_user_id.in_(member_rm_ids)).all()
    visible_ids = [e.keycloak_user_id for e in emps if e.keycloak_user_id]
    if not visible_ids:
        return {"total": 0, "records": []}

    query = db.query(Attendance).filter(Attendance.keycloak_user_id.in_(visible_ids))
    if from_date:
        try:
            query = query.filter(Attendance.attendance_date >= date.fromisoformat(from_date))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid from_date format.")
    if to_date:
        try:
            query = query.filter(Attendance.attendance_date <= date.fromisoformat(to_date))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid to_date format.")

    records = query.order_by(Attendance.attendance_date.desc()).all()

    result = []
    for a in records:
        rec = _to_response(db, a)
        emp = db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id == a.keycloak_user_id).first()
        if emp:
            rec["userEmail"] = emp.user_email
            rec["userName"] = f"{emp.first_name} {emp.last_name}".strip()
            rec["userDesignation"] = emp.designation

        rec["derivedStatus"] = "shift_ended" if a.check_out_time else "in_progress"
        rec["isAtAssignedLocation"] = a.location_id is not None and a.work_location_status == "OFFICE"
        rec["dayName"] = a.attendance_date.strftime("%A") if a.attendance_date else None

        shift = db.query(Shift).filter(
            and_(Shift.keycloak_user_id == a.keycloak_user_id, Shift.date == a.attendance_date)
        ).first()
        rec["shiftProject"] = shift.project_name if shift else None

        leave = db.query(Leave).filter(
            and_(
                Leave.keycloak_user_id == a.keycloak_user_id,
                Leave.approval_status == "approved",
                Leave.start_date <= a.attendance_date,
                Leave.end_date >= a.attendance_date,
            )
        ).first()
        rec["onLeave"] = leave is not None
        rec["leaveType"] = leave.leave_type if leave else None
        rec["leaveStartDate"] = leave.start_date.isoformat() if leave and leave.start_date else None
        rec["leaveEndDate"] = leave.end_date.isoformat() if leave and leave.end_date else None
        rec["leaveStartDay"] = leave.start_date.strftime("%A") if leave and leave.start_date else None
        rec["leaveEndDay"] = leave.end_date.strftime("%A") if leave and leave.end_date else None

        if a.check_out_time:
            if a.modified_by:
                ended_by_emp = db.query(EmployeeMaster).filter(EmployeeMaster.keycloak_user_id == a.modified_by).first()
                rec["endedBy"] = f"{ended_by_emp.first_name} {ended_by_emp.last_name}".strip() if ended_by_emp else a.modified_by
                rec["endedByRole"] = ended_by_emp.designation if ended_by_emp else None
            elif emp:
                rec["endedBy"] = f"{emp.first_name} {emp.last_name}".strip()
                rec["endedByRole"] = emp.designation
            else:
                rec["endedBy"] = None
                rec["endedByRole"] = None
        else:
            rec["endedBy"] = None
            rec["endedByRole"] = None

        result.append(rec)

    return {
        "total": len(result),
        "records": result,
    }
